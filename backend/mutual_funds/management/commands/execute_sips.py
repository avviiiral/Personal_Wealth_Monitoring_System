from django.contrib.auth.models import User
from django.core.management.base import (
    BaseCommand,
    CommandError,
)

from mutual_funds.services.holding_engine import (
    MutualFundHoldingEngine,
)

from mutual_funds.services.sip_engine import (
    SIPEngine,
)


class Command(BaseCommand):

    help = (
        "Execute all due SIP installments "
        "for a user."
    )

    def add_arguments(self, parser):

        parser.add_argument(
            "--user-id",
            type=int,
            required=True,
        )

    def handle(self, *args, **options):

        user_id = options["user_id"]

        try:

            user = User.objects.get(
                id=user_id
            )

        except User.DoesNotExist:

            raise CommandError(
                f"User {user_id} does not exist."
            )

        due_sips = SIPEngine.get_due_sips(
            user
        )

        if not due_sips:

            self.stdout.write(
                self.style.WARNING(
                    "No SIP installments are currently due."
                )
            )

            return

        transactions = []

        for sip in due_sips:

            transaction_record = (
                SIPEngine.execute_sip(
                    sip
                )
            )

            transactions.append(
                transaction_record
            )

            self.stdout.write(
                self.style.SUCCESS(
                    f"SIP executed: "
                    f"{sip.scheme.scheme_name}"
                )
            )

            self.stdout.write(
                f"Amount: ₹{sip.amount}"
            )

            self.stdout.write(
                f"Units: "
                f"{transaction_record.units}"
            )

            self.stdout.write(
                f"NAV: "
                f"{transaction_record.nav}"
            )

        # Rebuild holdings after all SIP transactions.

        schemes = set(
            transaction.scheme
            for transaction in transactions
        )

        for scheme in schemes:

            (
                MutualFundHoldingEngine
                .rebuild_holding(
                    scheme
                )
            )

        self.stdout.write(
            self.style.SUCCESS(
                f"Executed {len(transactions)} "
                f"SIP installment(s)."
            )
        )