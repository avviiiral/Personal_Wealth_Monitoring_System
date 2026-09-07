from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from investments.services.transaction_import import (
    TransactionImportError,
    TransactionImporter,
)


User = get_user_model()

DEFAULT_TRANSACTION_FILE = (
    Path(__file__).resolve().parents[3]
    / "data"
    / "transactions.xlsx"
)


class Command(BaseCommand):
    """
    One-time (repeatable) import of transaction data from an
    Excel/CSV workbook into the database.

    This wraps the exact same TransactionImporter used by:
        - the interactive upload endpoint
          (POST /api/investments/import-transactions/)
        - the previous per-request file-sync that used to run
          on every call to GET /api/portfolio/tree/

    so imported data, hierarchy, and calculations are identical
    to what the application already produced from these paths.

    Safe to run more than once: TransactionImporter already
    deduplicates rows via a content hash (Transaction.source_key,
    enforced by a unique DB constraint) for investment
    transactions, and via a field-based existence check for
    mutual fund transactions. Re-running with the same file
    (or a workbook with new rows appended) only inserts rows
    that are not already present.

    Usage:
        python manage.py import_transactions --username <user>
        python manage.py import_transactions --username <user> --file /path/to/transactions.xlsx
        python manage.py import_transactions --all-users
    """

    help = (
        "Import transaction data from an Excel (.xlsx) or CSV "
        "workbook into the database. Safe to re-run."
    )

    def add_arguments(self, parser):

        parser.add_argument(
            "--file",
            type=str,
            default=None,
            help=(
                "Path to the transaction workbook. Defaults to "
                f"{DEFAULT_TRANSACTION_FILE}"
            ),
        )

        owner_group = parser.add_mutually_exclusive_group(
            required=True
        )

        owner_group.add_argument(
            "--username",
            type=str,
            help="Username to import transactions for.",
        )

        owner_group.add_argument(
            "--user-id",
            type=int,
            help="User ID to import transactions for.",
        )

        owner_group.add_argument(
            "--all-users",
            action="store_true",
            help=(
                "Import the same workbook for every existing "
                "user."
            ),
        )

    def handle(self, *args, **options):

        file_arg = options.get("file")

        file_path = (
            Path(file_arg)
            if file_arg
            else DEFAULT_TRANSACTION_FILE
        )

        if not file_path.exists():
            raise CommandError(
                f"Transaction file not found: {file_path}"
            )

        if options.get("all_users"):
            owners = list(User.objects.all())

            if not owners:
                self.stdout.write(
                    "No users exist. Nothing to import."
                )
                return

        elif options.get("username"):
            try:
                owners = [
                    User.objects.get(
                        username=options["username"]
                    )
                ]
            except User.DoesNotExist:
                raise CommandError(
                    "User with username "
                    f"'{options['username']}' does not exist."
                )

        else:
            try:
                owners = [
                    User.objects.get(id=options["user_id"])
                ]
            except User.DoesNotExist:
                raise CommandError(
                    f"User with ID {options['user_id']} "
                    "does not exist."
                )

        for owner in owners:
            self._import_for_owner(file_path, owner)

    def _import_for_owner(self, file_path, owner):

        with open(file_path, "rb") as transaction_file:
            try:
                result = TransactionImporter.import_file(
                    file=transaction_file,
                    owner=owner,
                )

            except TransactionImportError as exc:
                raise CommandError(
                    f"[{owner.username}] Import failed: {exc}"
                )

        self.stdout.write(
            self.style.SUCCESS(
                f"[{owner.username}] Imported "
                f"{result['imported_investments']} investment "
                "transaction(s) and "
                f"{result['imported_mutual_funds']} mutual fund "
                f"transaction(s); skipped "
                f"{result['skipped_duplicates']} duplicate(s) "
                f"already in the database."
            )
        )