"""Delete PWMS investment and mutual-fund transaction ledger rows only."""
from django.core.management.base import BaseCommand, CommandError
from django.db import connections, transaction
from django.apps import apps

from users.management.reset_utils import prompt_for_confirmation


TRANSACTION_MODELS = (
    ("investments", "Transaction"),
    ("mutual_funds", "MutualFundTransaction"),
)


class Command(BaseCommand):
    help = (
        "Delete all investment and mutual-fund transaction ledger rows. "
        "Transaction edit history is cascade-deleted; SIP installment links "
        "are set to NULL. Assets, holdings, SIP instructions, reference data "
        "and user accounts are not directly deleted."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--database",
            default="default",
            help="Database alias to use (default: default).",
        )
        parser.add_argument(
            "--noinput",
            "--no-input",
            action="store_true",
            dest="noinput",
            help="Run without asking for confirmation.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show transaction counts without deleting data.",
        )

    def handle(self, *args, **options):
        alias = options["database"]
        if alias not in connections:
            raise CommandError(f"Unknown database alias: {alias}")

        connection = connections[alias]
        if connection.vendor not in {"postgresql", "sqlite"}:
            raise CommandError(
                f"Unsupported database vendor '{connection.vendor}'. "
                "PWMS reset commands support PostgreSQL and SQLite."
            )

        models = []
        total = 0
        for app_label, model_name in TRANSACTION_MODELS:
            try:
                model = apps.get_model(app_label, model_name)
            except LookupError as exc:
                raise CommandError(
                    f"Required transaction model {app_label}.{model_name} "
                    "is not installed. Aborting without deleting data."
                ) from exc
            count = model.objects.using(alias).count()
            models.append((model, count))
            total += count

        self.stdout.write(f"Database alias: {alias}")
        self.stdout.write(f"Database engine: {connection.vendor}")
        for model, count in models:
            self.stdout.write(
                f"  {model._meta.label}: {count:,} transaction rows"
            )
        self.stdout.write("Django migration history: PRESERVED")
        self.stdout.write("Non-transaction data: PRESERVED")

        if options["dry_run"]:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Dry run complete. {total:,} transaction rows selected; "
                    "no data was changed."
                )
            )
            return

        self.noinput = options["noinput"]
        if not prompt_for_confirmation(
            self,
            alias,
            "delete ALL investment and mutual-fund transaction ledger rows",
        ):
            return

        deleted_total = 0
        deleted_by_model = {}
        try:
            with transaction.atomic(using=alias):
                # QuerySet.delete() invokes Django's on_delete collector:
                # TransactionEditHistory is deleted with its transaction,
                # while SIPInstallment.transaction is set to NULL.
                for model, _count in models:
                    deleted, details = model.objects.using(alias).all().delete()
                    deleted_total += deleted
                    deleted_by_model[model._meta.label] = details
        except Exception as exc:
            raise CommandError(
                "Transaction reset failed and was rolled back. "
                f"Original error: {exc}"
            ) from exc

        self.stdout.write(
            self.style.SUCCESS(
                f"Deleted {deleted_total:,} rows across the two transaction "
                "ledgers and their Django-cascaded dependents."
            )
        )
        for label, details in deleted_by_model.items():
            self.stdout.write(f"  {label}: {details}")
        self.stdout.write(
            "Preserved: users, families, assets, holdings, portfolio positions, "
            "SIP instructions/installments, market data, MF master data and "
            "other non-transaction records."
        )
