from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError

from investments.models import Asset, Transaction

from market_data.services.market_data_manager import (
    MarketDataManager,
)

from market_data.services.mutual_fund_history_backfill import (
    MutualFundHistoryBackfillService,
)

from market_data.services.yahoo_finance import (
    YahooFinanceService,
)

from portfolio.services.holding_engine import (
    HoldingCalculationEngine,
)


class Command(BaseCommand):
    """
    Backfills historical price data starting from each asset's
    earliest transaction (buy) date, instead of only the latest
    price/NAV:

        STOCK / ETF   -> Yahoo Finance daily history
        MUTUAL_FUND   -> AMFI historical NAV

    Existing MarketPrice rows are updated in place (never
    duplicated), so this is safe to run more than once.

    BOND assets are not covered here; their price source only
    exposes the latest reported trade, not a historical series.
    """

    help = (
        "Backfill historical Stock/ETF prices and Mutual Fund "
        "NAV from each asset's earliest transaction date."
    )

    def add_arguments(self, parser):

        parser.add_argument(
            "--user-id",
            type=int,
            required=False,
            help=(
                "Only backfill assets belonging to this user. "
                "If omitted, all users' assets are backfilled."
            ),
        )

    def handle(self, *args, **options):

        user_id = options.get("user_id")

        assets = Asset.objects.filter(
            is_active=True,
            category__in=[
                "STOCK",
                "ETF",
                "MUTUAL_FUND",
            ],
        )

        if user_id:

            try:
                User.objects.get(id=user_id)

            except User.DoesNotExist:
                raise CommandError(
                    f"User with ID {user_id} does not exist."
                )

            assets = assets.filter(
                owner_id=user_id
            )

        assets = list(assets)

        stock_etf_assets = [
            asset
            for asset in assets
            if asset.category in ("STOCK", "ETF")
        ]

        mf_assets = [
            asset
            for asset in assets
            if asset.category == "MUTUAL_FUND"
        ]

        self._backfill_stock_etf(stock_etf_assets)
        self._backfill_mutual_funds(mf_assets)

    def _backfill_stock_etf(self, assets):

        self.stdout.write(
            self.style.NOTICE(
                f"Backfilling {len(assets)} "
                "Stock/ETF asset(s)..."
            )
        )

        for asset in assets:

            earliest_transaction_date = (
                Transaction.objects
                .filter(asset=asset)
                .order_by("transaction_date")
                .values_list(
                    "transaction_date",
                    flat=True,
                )
                .first()
            )

            if earliest_transaction_date is None:

                self.stdout.write(
                    f"  {asset.name}: no transactions, "
                    "skipped."
                )

                continue

            try:
                symbol = (
                    MarketDataManager
                    .resolve_asset_symbol(
                        asset
                    )
                )

            except Exception as exc:

                self.stdout.write(
                    self.style.WARNING(
                        f"  {asset.name}: unable to "
                        f"resolve symbol - {exc}"
                    )
                )

                continue

            if not symbol:

                self.stdout.write(
                    self.style.WARNING(
                        f"  {asset.name}: unable to "
                        "resolve symbol, skipped."
                    )
                )

                continue

            try:

                records = (
                    YahooFinanceService
                    .save_history(
                        asset=asset,
                        symbol=symbol,
                        start=earliest_transaction_date,
                    )
                )

                HoldingCalculationEngine.rebuild_holding(
                    asset
                )

                self.stdout.write(
                    self.style.SUCCESS(
                        f"  {asset.name}: {records} "
                        f"record(s) from "
                        f"{earliest_transaction_date}."
                    )
                )

            except Exception as exc:

                self.stdout.write(
                    self.style.ERROR(
                        f"  {asset.name}: failed - {exc}"
                    )
                )

    def _backfill_mutual_funds(self, assets):

        self.stdout.write(
            self.style.NOTICE(
                f"Backfilling {len(assets)} "
                "Mutual Fund asset(s)..."
            )
        )

        result = (
            MutualFundHistoryBackfillService
            .backfill_for_assets(assets)
        )

        self.stdout.write(
            self.style.SUCCESS(
                "  Mutual funds processed: "
                f"{result['assets']}, records written: "
                f"{result['records_written']}, AMFI "
                f"chunks downloaded: {result['chunks']}."
            )
        )

        for asset in assets:

            try:
                HoldingCalculationEngine.rebuild_holding(
                    asset
                )

            except Exception as exc:

                self.stdout.write(
                    self.style.ERROR(
                        f"  {asset.name}: holding rebuild "
                        f"failed - {exc}"
                    )
                )