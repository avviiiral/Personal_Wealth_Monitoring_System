import time

from django.core.management.base import BaseCommand

from investments.models import SecurityMaster
from market_data.services.security_resolver import SecurityResolver


class Command(BaseCommand):
    """
    Refresh the LIVE-tracked SecurityMaster fields — sector,
    pe_ratio, pb_ratio, roe — from Yahoo Finance, for every
    SecurityMaster row that can be resolved to a Yahoo ticker.

    Unlike link_security_master and load_security_master_data
    (which only ever fill an EMPTY field, protecting anything
    already set), this command ALWAYS overwrites those four
    fields on every successful run — that's the point of it: it's
    meant to run unattended (e.g. nightly, via Task Scheduler /
    cron) and keep them current without a human re-running a batch
    each time. It never touches amc_name, credit_rating, ytm,
    modified_duration, average_maturity, or cap_type — those come
    from different, slower-moving sources (AMC data, credit rating
    agencies, AMFI's twice-yearly categorisation) and are not
    something Yahoo Finance provides reliably for Indian equities.

    Symbol resolution reuses SecurityResolver.resolve_yahoo_symbol
    (market_data app) rather than re-deriving ISIN -> ticker logic
    — same resolver already used elsewhere in this codebase for
    price history. A row that can't be resolved to a symbol is
    skipped and reported, not guessed at.

    Dry-run by default: fetches and prints what WOULD change,
    without writing anything. Pass --apply to actually save.

    NOTE ON RELIABILITY: this pulls from Yahoo Finance via the
    unofficial `yfinance` package, the same one already used for
    price history in this codebase (see market_data/services/
    yahoo_finance.py, which wraps calls in a curl_cffi session
    impersonating a browser — Yahoo has been known to rate-limit
    or block plain requests). This command reuses that same
    session pattern. It is NOT an official, guaranteed-uptime data
    feed — expect occasional failures or gaps, and treat this as
    "best-effort nightly refresh for a personal dashboard", not a
    production financial data pipeline.
    """

    help = (
        "Refresh SecurityMaster sector/pe_ratio/pb_ratio/roe from "
        "Yahoo Finance for every resolvable security. Dry-run by "
        "default; pass --apply to write. Intended to run nightly "
        "via a scheduled task."
    )

    # Be gentle — Yahoo has been known to rate-limit yfinance
    # traffic that comes in too fast.
    DELAY_BETWEEN_REQUESTS_SECONDS = 1.0

    def add_arguments(self, parser):

        parser.add_argument(
            "--apply",
            action="store_true",
            help="Actually save the refreshed values. Without this flag, only prints a report.",
        )

        parser.add_argument(
            "--limit",
            type=int,
            help="Only process the first N SecurityMaster rows (useful for a first test run).",
        )

    def handle(self, *args, **options):

        apply_changes = options.get("apply", False)
        limit = options.get("limit")

        # Deferred import: yfinance/curl_cffi are heavy, and every
        # other management command in this app should keep working
        # even if this one's dependencies aren't installed yet.
        try:
            import yfinance as yf
            from curl_cffi import requests as curl_requests
        except ImportError as exc:
            self.stderr.write(
                self.style.ERROR(
                    f"Missing dependency: {exc}. Run: "
                    "pip install yfinance curl_cffi"
                )
            )
            return

        queryset = SecurityMaster.objects.all().order_by("id")

        if limit:
            queryset = queryset[:limit]

        session = curl_requests.Session(
            impersonate="chrome",
            doh_url="https://1.1.1.1/dns-query",
        )

        updated = 0
        unresolved = []
        fetch_failed = []

        for security_master in queryset:

            try:
                yahoo_symbol = SecurityResolver.resolve_yahoo_symbol(
                    isin=security_master.isin,
                    name=security_master.asset_name,
                )
            except ValueError:
                unresolved.append(security_master.asset_name)
                continue

            try:
                ticker = yf.Ticker(yahoo_symbol, session=session)
                info = ticker.info
            except Exception as exc:
                fetch_failed.append((security_master.asset_name, yahoo_symbol, str(exc)))
                continue

            if not info or info.get("regularMarketPrice") is None:
                fetch_failed.append(
                    (security_master.asset_name, yahoo_symbol, "empty/invalid response")
                )
                continue

            sector = info.get("sector")
            pe_ratio = info.get("trailingPE")
            pb_ratio = info.get("priceToBook")

            # yfinance reports ROE as a fraction (0.234), not a
            # percentage — SecurityMaster.roe is stored as a
            # percentage (23.4), matching every value entered by
            # hand so far in this project.
            raw_roe = info.get("returnOnEquity")
            roe = round(raw_roe * 100, 2) if raw_roe is not None else None

            changes = {}

            if sector and sector != security_master.sector:
                changes["sector"] = (security_master.sector, sector)

            if pe_ratio is not None and pe_ratio != security_master.pe_ratio:
                changes["pe_ratio"] = (security_master.pe_ratio, round(pe_ratio, 2))

            if pb_ratio is not None and pb_ratio != security_master.pb_ratio:
                changes["pb_ratio"] = (security_master.pb_ratio, round(pb_ratio, 2))

            if roe is not None and roe != security_master.roe:
                changes["roe"] = (security_master.roe, roe)

            if changes:
                self.stdout.write(f"  {security_master.asset_name!r} ({yahoo_symbol})")

                for field, (old, new) in changes.items():
                    self.stdout.write(f"      {field}: {old!r} -> {new!r}")

                    if apply_changes:
                        setattr(security_master, field, new)

                if apply_changes:
                    security_master.save(update_fields=list(changes.keys()))

                updated += 1

            time.sleep(self.DELAY_BETWEEN_REQUESTS_SECONDS)

        self.stdout.write("")

        if apply_changes:
            self.stdout.write(self.style.SUCCESS(f"Refreshed {updated} SecurityMaster row(s)."))
        else:
            self.stdout.write(
                self.style.WARNING(
                    f"DRY RUN — would refresh {updated} row(s). Re-run with --apply to save."
                )
            )

        if unresolved:
            self.stdout.write(
                f"{len(unresolved)} row(s) could not be resolved to a Yahoo symbol: "
                + ", ".join(unresolved)
            )

        if fetch_failed:
            self.stdout.write(
                self.style.WARNING(f"{len(fetch_failed)} row(s) failed to fetch:")
            )

            for name, symbol, error in fetch_failed:
                self.stdout.write(f"  {name!r} ({symbol}): {error}")
