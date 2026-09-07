from django.contrib.auth.models import User
from django.core.management.base import (
    BaseCommand,
    CommandError,
)

from mutual_funds.models import MutualFundScheme

from mutual_funds.services.holding_engine import (
    MutualFundHoldingEngine,
)


class Command(BaseCommand):

    help = (
        "Rebuild mutual-fund holdings from "
        "mutual-fund transactions."
    )

    def add_arguments(self, parser):

        parser.add_argument(
            "--user-id",
            type=int,
            help="User ID.",
        )

        parser.add_argument(
            "--scheme-id",
            type=int,
            help="Specific mutual-fund scheme ID.",
        )

    def handle(self, *args, **options):

        user_id = options.get("user_id")
        scheme_id = options.get("scheme_id")

        if scheme_id:

            try:

                scheme = (
                    MutualFundScheme.objects
                    .get(id=scheme_id)
                )

            except MutualFundScheme.DoesNotExist:

                raise CommandError(
                    f"Scheme with ID {scheme_id} "
                    "does not exist."
                )

            holding = (
                MutualFundHoldingEngine
                .rebuild_holding(scheme)
            )

            self.stdout.write(
                self.style.SUCCESS(
                    f"MF holding rebuilt: "
                    f"{scheme.scheme_name}"
                )
            )

            self.stdout.write(
                f"Units: {holding.units}"
            )

            self.stdout.write(
                f"Invested Value: "
                f"₹{holding.invested_value}"
            )

            self.stdout.write(
                f"Average NAV: "
                f"{holding.average_nav}"
            )

            self.stdout.write(
                f"Current NAV: "
                f"{holding.current_nav}"
            )

            self.stdout.write(
                f"Current Value: "
                f"₹{holding.current_value}"
            )

            self.stdout.write(
                f"Unrealized P&L: "
                f"₹{holding.unrealized_pnl}"
            )

            return

        if user_id:

            try:

                user = User.objects.get(
                    id=user_id
                )

            except User.DoesNotExist:

                raise CommandError(
                    f"User with ID {user_id} "
                    "does not exist."
                )

            holdings = (
                MutualFundHoldingEngine
                .rebuild_all_for_user(user)
            )

            self.stdout.write(
                self.style.SUCCESS(
                    f"Rebuilt {len(holdings)} "
                    "mutual-fund holdings."
                )
            )

            return

        raise CommandError(
            "Provide either --user-id "
            "or --scheme-id."
        )