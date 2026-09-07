from django.contrib.auth.models import User
from django.core.management.base import (
    BaseCommand,
    CommandError,
)

from mutual_funds.models import SIP

from mutual_funds.services.sip_installments import (
    SIPInstallmentService,
)

from mutual_funds.services.sip_reconciliation import (
    SIPInstallmentReconciliationService,
)


class Command(BaseCommand):

    help = (
        "Generate, synchronize and reconcile "
        "SIP installments."
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

        sips = (
            SIP.objects
            .filter(
                owner=user,
            )
            .select_related("scheme")
        )

        total_created = 0
        total_due = 0
        total_reconciled = 0

        for sip in sips:

            result = (
                SIPInstallmentService
                .synchronize_sip(
                    sip
                )
            )

            reconciled = (
                SIPInstallmentReconciliationService
                .reconcile_sip(
                    sip
                )
            )

            total_created += (
                result["created"]
            )

            total_due += (
                result["updated_to_due"]
            )

            total_reconciled += (
                reconciled
            )

            self.stdout.write(
                f"{sip.scheme.scheme_name}: "
                f"created={result['created']}, "
                f"due={result['updated_to_due']}, "
                f"reconciled={reconciled}"
            )

        self.stdout.write(
            self.style.SUCCESS(
                "SIP installment synchronization "
                "and reconciliation completed."
            )
        )

        self.stdout.write(
            f"Total installments created: "
            f"{total_created}"
        )

        self.stdout.write(
            f"Total installments marked due: "
            f"{total_due}"
        )

        self.stdout.write(
            f"Total installments reconciled: "
            f"{total_reconciled}"
        )