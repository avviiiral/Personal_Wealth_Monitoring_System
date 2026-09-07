from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction as db_transaction

from investments.models import (
    Asset,
    PortfolioPosition,
    Transaction,
    TransactionSource,
)

from investments.services.file_transaction_sync import (
    FileTransactionSyncService,
)

from investments.services.transaction_import import (
    TransactionImportError,
    TransactionImporter,
)

from portfolio.services.portfolio_position_engine import (
    PortfolioPositionEngine,
)


class Command(BaseCommand):
    """
    Repairs Excel-imported investment data whose Asset identity
    was previously computed from the Excel "Asset Name" column
    only.

    For several sub-classes (e.g. PMS strategies, AIFs, or a
    generic "Direct Equity" bucket) that column holds a shared
    product/strategy label rather than the actual security, so
    different real securities/underlyings could previously end
    up sharing a single Asset record - inflating quantity and
    hiding holdings.

    Because Transaction rows do not retain the source ISIN
    independently of Asset, the only way to safely reconstruct
    correct Asset identity for already-imported rows is to
    remove the affected Excel-sourced Transaction/Asset records
    and re-import them from the source workbook using the
    corrected identity resolution logic
    (TransactionImporter.resolve_security_identity_name).

    This command never touches:
        - Transactions with source=MANUAL
        - Mutual Fund transactions/schemes (separate pipeline)

    It is safe to run multiple times.
    """

    help = (
        "Repair Excel-imported Asset identity by re-importing "
        "from the synchronized transaction workbook."
    )

    def add_arguments(self, parser):

        parser.add_argument(
            "--user-id",
            type=int,
            help=(
                "User ID to repair. If omitted, all users "
                "with Excel-sourced transactions are repaired."
            ),
        )

    def handle(self, *args, **options):

        user_id = options.get("user_id")

        if user_id:

            try:
                owners = [
                    User.objects.get(id=user_id)
                ]

            except User.DoesNotExist:
                raise CommandError(
                    f"User with ID {user_id} does not exist."
                )

        else:

            owners = list(
                User.objects.filter(
                    transactions__source=(
                        TransactionSource.EXCEL
                    )
                )
                .distinct()
            )

        if not owners:
            self.stdout.write(
                "No users with Excel-sourced transactions "
                "were found."
            )
            return

        for owner in owners:
            self._repair_owner(owner)

    def _repair_owner(self, owner):

        with db_transaction.atomic():

            excel_transactions = Transaction.objects.filter(
                owner=owner,
                source=TransactionSource.EXCEL,
            )

            affected_asset_ids = set(
                excel_transactions
                .values_list("asset_id", flat=True)
                .distinct()
            )

            deleted_count = excel_transactions.count()

            excel_transactions.delete()

            # ------------------------------------------------
            # Remove Asset records that only existed because of
            # the Excel import and now have no transactions
            # left referencing them at all (Excel or Manual).
            # Any Asset still used by a Manual transaction is
            # preserved.
            # ------------------------------------------------
            orphaned_assets = (
                Asset.objects
                .filter(
                    id__in=affected_asset_ids,
                    owner=owner,
                )
                .exclude(
                    transactions__isnull=False,
                )
            )

            removed_assets = list(
                orphaned_assets.values_list(
                    "id",
                    flat=True,
                )
            )

            orphaned_assets.delete()

            # ------------------------------------------------
            # Stale portfolio positions for the removed assets.
            # ------------------------------------------------
            PortfolioPosition.objects.filter(
                owner=owner,
                asset_id__in=removed_assets,
            ).delete()

        self.stdout.write(
            f"[{owner.username}] Removed "
            f"{deleted_count} Excel transaction(s) and "
            f"{len(removed_assets)} orphaned asset(s)."
        )

        # ----------------------------------------------------
        # Force a clean re-import from the synchronized
        # workbook so Asset identity is rebuilt using the
        # corrected resolution logic.
        # ----------------------------------------------------
        try:
            file_path = FileTransactionSyncService.get_file()

            with open(file_path, "rb") as transaction_file:
                result = TransactionImporter.import_file(
                    file=transaction_file,
                    owner=owner,
                )

        except FileNotFoundError:
            self.stdout.write(
                self.style.WARNING(
                    f"[{owner.username}] No synchronized "
                    "workbook found; transactions were "
                    "removed but not re-imported. Upload the "
                    "transaction file again to complete the "
                    "repair."
                )
            )
            return

        except TransactionImportError as exc:
            raise CommandError(
                f"[{owner.username}] Re-import failed: {exc}"
            )

        FileTransactionSyncService._last_synced_mtime_ns = (
            FileTransactionSyncService.get_file_version()
        )

        PortfolioPositionEngine.rebuild_all_for_user(owner)

        self.stdout.write(
            self.style.SUCCESS(
                f"[{owner.username}] Re-imported "
                f"{result['total_imported']} transaction(s) "
                "with corrected asset identity."
            )
        )