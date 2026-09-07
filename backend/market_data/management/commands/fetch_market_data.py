from django.core.management.base import BaseCommand, CommandError

from investments.models import Asset
from market_data.services.yahoo_finance import YahooFinanceService


class Command(BaseCommand):

    help = "Fetch historical market data from Yahoo Finance."

    def add_arguments(self, parser):

        parser.add_argument(
            "--symbol",
            type=str,
            help="Yahoo Finance symbol, e.g. RELIANCE.NS",
        )

        parser.add_argument(
            "--asset-id",
            type=int,
            help="PWMS Asset ID.",
        )

        parser.add_argument(
            "--period",
            type=str,
            default="1y",
            help="Yahoo Finance period, e.g. 1mo, 3mo, 6mo, 1y, 5y.",
        )

    def handle(self, *args, **options):

        symbol = options.get("symbol")
        asset_id = options.get("asset_id")
        period = options.get("period")

        if not symbol:
            raise CommandError(
                "--symbol is required."
            )

        if not asset_id:
            raise CommandError(
                "--asset-id is required."
            )

        try:
            asset = Asset.objects.get(
                id=asset_id
            )
        except Asset.DoesNotExist:
            raise CommandError(
                f"Asset with ID {asset_id} does not exist."
            )

        self.stdout.write(
            self.style.NOTICE(
                f"Fetching {symbol} for asset '{asset.name}'..."
            )
        )

        try:

            count = YahooFinanceService.save_history(
                asset=asset,
                symbol=symbol,
                period=period,
            )

        except Exception as exc:

            raise CommandError(
                f"Market data fetch failed: {exc}"
            )

        self.stdout.write(
            self.style.SUCCESS(
                f"Successfully stored {count} market-price records."
            )
        )