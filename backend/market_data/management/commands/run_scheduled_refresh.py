from config.postgres_advisory_lock import with_postgres_advisory_lock
from django.contrib.auth.models import User
from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.utils import timezone

from mutual_funds.services.amfi import AMFIService
from portfolio.mis_history_prefetch import MISHistoryPrefetch
from watchlist.services.performance import AMFIPerformanceService
from watchlist.services.universe import AMFIUniverseService


class Command(BaseCommand):
    """
    Single entry point for every external-data refresh PWMS relies
    on — the one command a scheduler (Windows Task Scheduler today;
    Celery Beat once the project is containerized) needs to call.

    Runs, in dependency order:

        1. refresh_investment_amfi        - latest AMFI NAVs only for
                                            mutual-fund ISINs actually held
                                            in investments.
        2. update_market_prices            - live Stock/ETF prices
                                            (Yahoo). Also
                                            auto-refreshes
                                            security_master.xlsx if
                                            a new ISIN shows up.
        4. refresh_security_master
           --apply                       - sector/pe_ratio/
                                            pb_ratio/roe (Yahoo).
                                            Runs after prices/NAV so
                                            the day's holdings are
                                            already current.
        5. sync_sip_installments
           (per user)                    - generate/reconcile due
                                            SIP installments.
        5. execute_sips (per user)       - execute installments
                                            that are now due. Runs
                                            after sync so nothing
                                            newly generated is
                                            missed on the same pass.
        6. monitor_portfolio_news        - news + portfolio-weighted
                                            alerts. Runs last so it
                                            sees the day's updated
                                            holdings/prices, not
                                            yesterday's.

    load_security_master_data, backfill_price_history, and
    rebuild_holdings/rebuild_mf_holdings are intentionally NOT
    included here: the first is a manual research file you edit
    by hand, and the other two are one-off/repair tools you run
    yourself when something looks wrong - not things that should
    silently re-run every night.

    Every command this calls is itself idempotent (see each
    command's own docstring), so this is safe to re-run, and one
    step failing does not stop the rest - failures are collected
    and reported at the end instead of aborting the whole run.
    """

    help = (
        "Run every scheduled external-data refresh (market prices, "
        "investment-driven AMFI NAVs by ISIN, security master ratios, SIP sync/execute, "
        "portfolio news) for every active user, in one call. "
        "Intended to be the single command a scheduler triggers "
        "nightly."
    )

    # Commands that operate across all users in one call - no
    # --user-id needed/accepted.
    GLOBAL_STEPS = [
        ("update_market_prices", {}),
        ("refresh_mis_reference_prices", {}),
    ]

    # Commands that require --user-id and must be run once per
    # active user.
    PER_USER_STEPS = [
        ("sync_sip_installments", {}),
        ("execute_sips", {}),
    ]

    # Global steps that must run AFTER the per-user steps above
    # (security master refresh wants that day's holdings already
    # in place; portfolio news wants the full day's picture).
    GLOBAL_STEPS_AFTER = [
        ("refresh_security_master", {"apply": True}),
        ("monitor_portfolio_news", {}),
    ]

    def add_arguments(self, parser):

        parser.add_argument(
            "--skip",
            action="append",
            default=[],
            help=(
                "Command name to skip for this run (repeatable), "
                "e.g. --skip monitor_portfolio_news --skip fetch_amfi_nav."
            ),
        )

    @with_postgres_advisory_lock("pwms_refresh_pipeline")
    def handle(self, *args, **options):

        skip = set(options.get("skip") or [])

        started = timezone.now()

        self.stdout.write("")
        self.stdout.write("=" * 60)
        self.stdout.write(f"PWMS SCHEDULED REFRESH — {started.isoformat()}")
        self.stdout.write("=" * 60)

        succeeded = []
        failed = []

        def run_step(command_name, kwargs, user_id=None):
            if command_name in skip:
                self.stdout.write(f"\n--- {command_name} (skipped) ---")
                return

            label = command_name if user_id is None else f"{command_name} (user {user_id})"

            self.stdout.write(f"\n--- {label} ---")

            call_kwargs = dict(kwargs)

            if user_id is not None:
                call_kwargs["user_id"] = user_id

            try:
                call_command(command_name, **call_kwargs)
                succeeded.append(label)
            except Exception as exc:
                failed.append((label, str(exc)))
                self.stderr.write(self.style.ERROR(f"{label} failed: {exc}"))

        for command_name, kwargs in self.GLOBAL_STEPS:
            run_step(command_name, kwargs)

        # BenchmarkPerformanceService calculates benchmark series on demand;
        # there is no BenchmarkDataRefreshService to invoke here. Keeping this
        # step out avoids the stale import that previously crashed the scheduler.

        active_user_ids = list(
            User.objects.filter(is_active=True).values_list("id", flat=True)
        )

        # Refresh only the AMFI schemes represented by actual investments.
        # Asset.ISIN is the primary mapping key; no full AMFI universe import
        # is performed here.
        if "refresh_investment_amfi" not in skip:
            self.stdout.write("\n--- refresh_investment_amfi ---")
            try:
                from mutual_funds.services.investment_amfi import InvestmentAMFIService

                result = InvestmentAMFIService.refresh_for_all_investments()
                succeeded.append("refresh_investment_amfi")
                self.stdout.write(self.style.SUCCESS(
                    "Investment AMFI refresh completed: "
                    f"requested_isins={result.get('requested_isins', 0)}, "
                    f"matched_isins={result.get('matched_isins', 0)}, "
                    f"schemes={result.get('schemes', 0)}, "
                    f"nav_records={result.get('nav_records', 0)}, "
                    f"unmatched={result.get('unmatched_isins', [])}"
                ))
            except Exception as exc:
                failed.append(("refresh_investment_amfi", str(exc)))
                self.stderr.write(
                    self.style.ERROR(f"refresh_investment_amfi failed: {exc}")
                )

        # Materialize only the latest NAVs for each user's owned mutual-fund
        # schemes before SIP execution and downstream refreshes.
        if "sync_owned_amfi_navs" not in skip:
            for user_id in active_user_ids:
                self.stdout.write(
                    f"\n--- sync_owned_amfi_navs (user {user_id}) ---"
                )
                try:
                    user = User.objects.get(id=user_id)
                    result = AMFIService.sync_owned_navs_from_master(user)
                    succeeded.append(
                        f"sync_owned_amfi_navs (user {user_id})"
                    )
                    self.stdout.write(self.style.SUCCESS(
                        "Owned AMFI NAVs synced: "
                        f"schemes={result.get('schemes', 0)}, "
                        f"matched={result.get('matched', 0)}, "
                        f"nav_records={result.get('nav_records', 0)}"
                    ))
                except Exception as exc:
                    failed.append(
                        (f"sync_owned_amfi_navs (user {user_id})", str(exc))
                    )
                    self.stderr.write(
                        self.style.ERROR(
                            f"sync_owned_amfi_navs (user {user_id}) failed: {exc}"
                        )
                    )

        # Keep the AMFI history the MIS report reads stored ahead of time
        # (held schemes only, missing range only), so opening MIS never
        # waits on AMFI.
        if "prefetch_mis_history" not in skip:
            self.stdout.write("\n--- prefetch_mis_history ---")
            try:
                result = MISHistoryPrefetch.run_for_all_families()
                succeeded.append("prefetch_mis_history")
                self.stdout.write(self.style.SUCCESS(
                    "MIS AMFI history prefetched: "
                    f"families={result.get('families', 0)}, "
                    f"schemes={result.get('schemes', 0)}, "
                    f"requests={result.get('requests', 0)}, "
                    f"failed={result.get('failed', 0)}"
                ))
            except Exception as exc:
                failed.append(("prefetch_mis_history", str(exc)))
                self.stderr.write(
                    self.style.ERROR(f"prefetch_mis_history failed: {exc}")
                )

        for user_id in active_user_ids:
            for command_name, kwargs in self.PER_USER_STEPS:
                run_step(command_name, kwargs, user_id=user_id)

        # Watch List universe/performance is global, not user-owned.
        if "refresh_watchlist_universe" not in skip:
            self.stdout.write("\n--- refresh_watchlist_universe ---")
            try:
                result = AMFIUniverseService.refresh()
                succeeded.append("refresh_watchlist_universe")
                self.stdout.write(self.style.SUCCESS(
                    f"Watch List universe refreshed: {result.get('discovered', 0)} schemes"
                ))
            except Exception as exc:
                failed.append(("refresh_watchlist_universe", str(exc)))
                self.stderr.write(self.style.ERROR(f"refresh_watchlist_universe failed: {exc}"))

        if "refresh_watchlist_performance" not in skip:
            self.stdout.write("\n--- refresh_watchlist_performance ---")
            try:
                result = AMFIPerformanceService.refresh()
                succeeded.append("refresh_watchlist_performance")
                self.stdout.write(self.style.SUCCESS(
                    f"Watch List performance refreshed: {result.get('metrics_updated', 0)} products"
                ))
            except Exception as exc:
                failed.append(("refresh_watchlist_performance", str(exc)))
                self.stderr.write(self.style.ERROR(f"refresh_watchlist_performance failed: {exc}"))

        for command_name, kwargs in self.GLOBAL_STEPS_AFTER:
            run_step(command_name, kwargs)

        elapsed = (timezone.now() - started).total_seconds()

        self.stdout.write("")
        self.stdout.write("=" * 60)
        self.stdout.write(
            f"DONE in {elapsed:.1f}s — "
            f"{len(succeeded)} succeeded, {len(failed)} failed."
        )

        if failed:
            self.stdout.write(self.style.WARNING("Failed steps:"))

            for label, error in failed:
                self.stdout.write(f"  {label}: {error}")

        self.stdout.write("=" * 60)