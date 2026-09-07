from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError

from investments.models import Asset
from portfolio.services.holding_engine import (
    HoldingCalculationEngine,
)


class Command(BaseCommand):

    help = "Rebuild portfolio holdings from transactions."

    def add_arguments(self, parser):

        parser.add_argument(
            "--user-id",
            type=int,
            help="User ID whose holdings should be rebuilt.",
        )

        parser.add_argument(
            "--asset-id",
            type=int,
            help="Specific asset ID to rebuild.",
        )

    def handle(self, *args, **options):

        user_id = options.get("user_id")
        asset_id = options.get("asset_id")

        if asset_id:

            try:
                asset = Asset.objects.get(
                    id=asset_id
                )
            except Asset.DoesNotExist:
                raise CommandError(
                    f"Asset with ID {asset_id} does not exist."
                )

            holding = (
                HoldingCalculationEngine
                .rebuild_holding(asset)
            )

            self.stdout.write(
                self.style.SUCCESS(
                    f"Holding rebuilt: {holding.asset.name}"
                )
            )

            self.stdout.write(
                f"Quantity: {holding.quantity}"
            )

            self.stdout.write(
                f"Invested Value: ₹{holding.invested_value}"
            )

            self.stdout.write(
                f"Current Value: ₹{holding.current_value}"
            )

            self.stdout.write(
                f"Unrealized P&L: ₹{holding.unrealized_pnl}"
            )

            return

        if user_id:

            try:
                user = User.objects.get(
                    id=user_id
                )
            except User.DoesNotExist:
                raise CommandError(
                    f"User with ID {user_id} does not exist."
                )

            holdings = (
                HoldingCalculationEngine
                .rebuild_all_for_user(user)
            )

            self.stdout.write(
                self.style.SUCCESS(
                    f"Rebuilt {len(holdings)} holdings."
                )
            )

            return

        raise CommandError(
            "Provide either --user-id or --asset-id."
        )