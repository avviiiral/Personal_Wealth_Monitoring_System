from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from mutual_funds.models import (
    MutualFundScheme,
    MutualFundTransaction,
)

from mutual_funds.services.nav_service import (
    MutualFundNAVService,
)


class Command(BaseCommand):

    help = (
        "Recalculate mutual fund transaction NAV "
        "and units using historical NAV data."
    )

    def add_arguments(self, parser):

        parser.add_argument(
            "--scheme-code",
            required=True,
            type=str,
        )

    @transaction.atomic
    def handle(self, *args, **options):

        scheme_code = options["scheme_code"]

        try:

            scheme = (
                MutualFundScheme.objects
                .get(
                    scheme_code=scheme_code
                )
            )

        except MutualFundScheme.DoesNotExist:

            raise CommandError(
                f"Scheme {scheme_code} does not exist."
            )

        transactions = (
            MutualFundTransaction.objects
            .filter(
                scheme=scheme
            )
            .order_by(
                "transaction_date",
                "id",
            )
        )

        if not transactions.exists():

            raise CommandError(
                "No transactions found."
            )

        updated = 0

        for tx in transactions:

            try:

                nav_record = (
                    MutualFundNAVService
                    .get_nav_for_date(
                        scheme,
                        tx.transaction_date,
                    )
                )

            except ValueError as exc:

                raise CommandError(
                    str(exc)
                )

            old_nav = tx.nav
            old_units = tx.units

            new_nav = nav_record.nav

            new_units = (
                tx.amount / new_nav
            )

            tx.nav = new_nav
            tx.units = new_units

            tx.save(
                update_fields=[
                    "nav",
                    "units",
                ]
            )

            updated += 1

            self.stdout.write(
                f"Transaction #{tx.id}: "
                f"{tx.transaction_date} | "
                f"NAV {old_nav} -> {new_nav} | "
                f"Units {old_units} -> {new_units} | "
                f"NAV date {nav_record.date}"
            )

        self.stdout.write("")

        self.stdout.write(
            self.style.SUCCESS(
                f"Updated transactions: {updated}"
            )
        )