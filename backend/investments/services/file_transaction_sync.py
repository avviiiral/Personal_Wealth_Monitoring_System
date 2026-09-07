from pathlib import Path
import time

from django.db import transaction
from django.db.utils import OperationalError

from investments.services.transaction_import import (
    TransactionImporter,
)


BASE_DIR = Path(__file__).resolve().parents[2]

TRANSACTION_FILE = (
    BASE_DIR / "data" / "transactions.xlsx"
)


class FileTransactionSyncService:

    _last_synced_mtime_ns = None

    MAX_RETRIES = 5
    RETRY_DELAY_SECONDS = 1

    @staticmethod
    def get_file():

        if not TRANSACTION_FILE.exists():

            raise FileNotFoundError(
                f"Transaction file not found: "
                f"{TRANSACTION_FILE}"
            )

        return TRANSACTION_FILE

    @classmethod
    def get_file_version(cls):

        return cls.get_file().stat().st_mtime_ns

    @classmethod
    def has_changed(cls):

        return (
            cls._last_synced_mtime_ns
            != cls.get_file_version()
        )

    @classmethod
    def sync(cls, owner):

        file_path = cls.get_file()

        current_version = (
            file_path.stat().st_mtime_ns
        )

        # --------------------------------------------------
        # Nothing changed since the previous successful sync
        # --------------------------------------------------

        if (
            cls._last_synced_mtime_ns
            == current_version
        ):

            return {
                "success": True,
                "changed": False,
                "message": (
                    "Transaction file is already "
                    "synchronized."
                ),
            }

        last_error = None

        # --------------------------------------------------
        # Import with retry protection
        # --------------------------------------------------

        for attempt in range(
            1,
            cls.MAX_RETRIES + 1,
        ):

            try:

                with transaction.atomic():

                    with open(
                        file_path,
                        "rb",
                    ) as transaction_file:

                        result = (
                            TransactionImporter
                            .import_file(
                                file=transaction_file,
                                owner=owner,
                            )
                        )

                # Only mark the file as synchronized
                # after the database transaction succeeds.
                cls._last_synced_mtime_ns = (
                    current_version
                )

                return {
                    "success": True,
                    "changed": True,
                    "message": (
                        "Transaction file synchronized "
                        "successfully."
                    ),
                    "result": result,
                }

            except OperationalError as exc:

                last_error = exc

                if (
                    "database is locked"
                    not in str(exc).lower()
                ):
                    raise

                if attempt < cls.MAX_RETRIES:

                    time.sleep(
                        cls.RETRY_DELAY_SECONDS
                    )

            except (
                PermissionError,
                OSError,
            ) as exc:

                last_error = exc

                if attempt < cls.MAX_RETRIES:

                    time.sleep(
                        cls.RETRY_DELAY_SECONDS
                    )

                else:
                    raise

        if last_error is not None:
            raise last_error

        raise RuntimeError(
            "Transaction file synchronization failed."
        )