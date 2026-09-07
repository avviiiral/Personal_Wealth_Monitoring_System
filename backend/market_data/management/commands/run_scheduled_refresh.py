from django.contrib.auth.models import User
from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.utils import timezone


class Command(BaseCommand):
    """
    Single entry point for every external-data refresh PWMS relies
    on — the one command a scheduler (Windows Task Scheduler today;
    Celery Beat once the project is containerized) needs to call.

    Runs, in dependency order:

        1. update_market_prices          - live Stock/ETF prices
                                            (Yahoo). Also
                                            auto-refreshes
                                            security_master.xlsx if
                                            a new ISIN shows up.
        2. fetch_amfi_nav (per user)     - mutual fund NAV (AMFI),
                                            latest day only.
        3. refresh_security_master
           --apply                       - sector/pe_ratio/
                                            pb_ratio/roe (Yahoo).
                                            Runs after prices/NAV so
                                            the day's holdings are
                                            already current.
        4. sync_sip_installments
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
        "mutual fund NAV, security master ratios, SIP sync/execute, "
        "portfolio news) for every active user, in one call. "
        "Intended to be the single command a scheduler triggers "
        "nightly."
    )

    # Commands that operate across all users in one call - no
    # --user-id needed/accepted.
    GLOBAL_STEPS = [
        ("update_market_prices", {}),
    ]

    # Commands that require --user-id and must be run once per
    # active user.
    PER_USER_STEPS = [
        ("fetch_amfi_nav", {}),
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

        active_user_ids = list(
            User.objects.filter(is_active=True).values_list("id", flat=True)
        )

        for user_id in active_user_ids:
            for command_name, kwargs in self.PER_USER_STEPS:
                run_step(command_name, kwargs, user_id=user_id)

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