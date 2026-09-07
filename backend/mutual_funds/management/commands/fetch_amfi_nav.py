from datetime import datetime

from django.contrib.auth.models import User
from django.core.management.base import (
    BaseCommand,
    CommandError,
)

from mutual_funds.services.amfi import AMFIService


class Command(BaseCommand):

    help = (
        "Download and import mutual-fund "
        "NAV data from AMFI."
    )

    def add_arguments(self, parser):

        parser.add_argument(
            "--user-id",
            type=int,
            required=True,
            help=(
                "User ID that will own the imported "
                "mutual-fund schemes."
            ),
        )

        parser.add_argument(
            "--from-date",
            type=str,
            required=False,
            help=(
                "Historical NAV start date in "
                "YYYY-MM-DD format."
            ),
        )

        parser.add_argument(
            "--to-date",
            type=str,
            required=False,
            help=(
                "Historical NAV end date in "
                "YYYY-MM-DD format."
            ),
        )

    def handle(self, *args, **options):

        user_id = options["user_id"]

        from_date_text = options.get(
            "from_date"
        )

        to_date_text = options.get(
            "to_date"
        )

        try:

            user = User.objects.get(
                id=user_id
            )

        except User.DoesNotExist:

            raise CommandError(
                f"User with ID {user_id} does not exist."
            )

        # --------------------------------------------------
        # Historical NAV import
        # --------------------------------------------------

        if (
            from_date_text
            or to_date_text
        ):

            if not (
                from_date_text
                and to_date_text
            ):

                raise CommandError(
                    "Both --from-date and --to-date "
                    "are required for historical NAV "
                    "import."
                )

            try:

                from_date = datetime.strptime(
                    from_date_text,
                    "%Y-%m-%d",
                ).date()

                to_date = datetime.strptime(
                    to_date_text,
                    "%Y-%m-%d",
                ).date()

            except ValueError:

                raise CommandError(
                    "Dates must use YYYY-MM-DD format."
                )

            if from_date > to_date:

                raise CommandError(
                    "--from-date cannot be after "
                    "--to-date."
                )

            self.stdout.write(
                self.style.NOTICE(
                    "Downloading historical AMFI "
                    f"NAV data from {from_date} "
                    f"to {to_date}..."
                )
            )

            try:

                result = (
                    AMFIService
                    .import_historical_navs(
                        user,
                        from_date,
                        to_date,
                    )
                )

            except Exception as exc:

                raise CommandError(
                    f"AMFI historical import failed: "
                    f"{exc}"
                )

            self.stdout.write(
                self.style.SUCCESS(
                    "AMFI historical NAV import "
                    "completed."
                )
            )

            self.stdout.write(
                f"Schemes processed: "
                f"{result['schemes']}"
            )

            self.stdout.write(
                f"NAV records processed: "
                f"{result['nav_records']}"
            )

            return

        # --------------------------------------------------
        # Latest NAV import
        # --------------------------------------------------

        self.stdout.write(
            self.style.NOTICE(
                "Downloading latest AMFI NAV data..."
            )
        )

        try:

            result = (
                AMFIService
                .import_latest_navs(user)
            )

        except Exception as exc:

            raise CommandError(
                f"AMFI import failed: {exc}"
            )

        self.stdout.write(
            self.style.SUCCESS(
                "AMFI NAV import completed."
            )
        )

        self.stdout.write(
            f"Schemes processed: "
            f"{result['schemes']}"
        )

        self.stdout.write(
            f"NAV records processed: "
            f"{result['nav_records']}"
        )