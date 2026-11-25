"""One-off repair: re-download the last N days of Yahoo prices.

Before the freshness fix, the first fetch of a trading day could store an
intraday price that was never replaced by the official close. This command
re-fetches the last N days (default 30) for every stock/ETF in the price loop
and overwrites those rows (insert-or-update on asset/date/source).

Do not run it on the production database before reviewing a --dry-run. SGBs
and bonds are not touched (their providers only report the latest value).
"""
from datetime import timedelta

from django.core.management.base import BaseCommand

from config.postgres_advisory_lock import with_postgres_advisory_lock
from config.scheduling.clock import now as price_now
from market_data.services.market_data_manager import MarketDataManager
from market_data.services.market_price_scope import price_loop_assets
from market_data.services.yahoo_finance import YahooFinanceService


class Command(BaseCommand):
    help = "Re-fetch the last N days of Yahoo prices to replace stored intraday values with official closes."

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=30)
        parser.add_argument("--dry-run", action="store_true", help="List the assets and date range; fetch nothing.")

    @with_postgres_advisory_lock("pwms_refresh_pipeline")
    def handle(self, *args, **options):
        days = max(1, int(options["days"]))
        today = price_now().date()
        start = today - timedelta(days=days)
        end = today + timedelta(days=1)
        assets = [
            asset for asset in price_loop_assets()
            if "SOVEREIGN GOLD BOND" not in (asset.name or "").upper() and asset.category != "BOND"
        ]
        self.stdout.write(f"Re-fetching {start} to {today} for {len(assets)} asset(s){' (dry run)' if options['dry_run'] else ''}.")
        updated = failed = records = 0
        for asset in assets:
            if options["dry_run"]:
                self.stdout.write(f"  would refetch {asset.name}")
                continue
            try:
                symbol = MarketDataManager.resolve_asset_symbol(asset)
                if not symbol:
                    raise ValueError("no Yahoo symbol")
                saved = YahooFinanceService.save_history(asset=asset, symbol=symbol, start=start, end=end)
                MarketDataManager._rebuild_holding(asset)
                updated += 1
                records += saved
            except Exception as exc:  # noqa: BLE001 - continue with the next asset
                failed += 1
                self.stdout.write(self.style.WARNING(f"  {asset.name}: {exc}"))
        self.stdout.write(self.style.SUCCESS(f"Done: updated={updated} failed={failed} records={records}"))
