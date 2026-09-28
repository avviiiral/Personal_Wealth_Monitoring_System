from datetime import datetime

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError

from mutual_funds.services.amfi import AMFIService


class Command(BaseCommand):
    help = (
        "Refresh the shared AMFI NAV master. "
        "Use --user-id only for a legacy family sync."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--user-id",
            type=int,
            required=False,
            help=(
                "Optional legacy user ID. AMFI is downloaded into the global "
                "master dataset; when supplied, the latest master data is "
                "also materialized into that user's family."
            ),
        )
        parser.add_argument(
            "--from-date",
            type=str,
            required=False,
            help="Historical NAV start date in YYYY-MM-DD format.",
        )
        parser.add_argument(
            "--to-date",
            type=str,
            required=False,
            help="Historical NAV end date in YYYY-MM-DD format.",
        )

    def handle(self, *args, **options):
        user = None
        user_id = options.get("user_id")

        if user_id is not None:
            try:
                user = User.objects.get(id=user_id)
            except User.DoesNotExist:
                raise CommandError(f"User with ID {user_id} does not exist.")

        from_date_text = options.get("from_date")
        to_date_text = options.get("to_date")

        if from_date_text or to_date_text:
            if not (from_date_text and to_date_text):
                raise CommandError(
                    "Both --from-date and --to-date are required for historical NAV import."
                )

            try:
                from_date = datetime.strptime(from_date_text, "%Y-%m-%d").date()
                to_date = datetime.strptime(to_date_text, "%Y-%m-%d").date()
            except ValueError:
                raise CommandError("Dates must use YYYY-MM-DD format.")

            if from_date > to_date:
                raise CommandError("--from-date cannot be after --to-date.")

            self.stdout.write(
                self.style.NOTICE(
                    f"Downloading historical AMFI NAV data from {from_date} to {to_date}..."
                )
            )

            try:
                result = AMFIService.import_historical_master_navs(
                    from_date,
                    to_date,
                )
            except Exception as exc:
                raise CommandError(f"AMFI historical master import failed: {exc}")

            self.stdout.write(self.style.SUCCESS("AMFI historical master import completed."))
            self.stdout.write(f"Schemes processed: {result['schemes']}")
            self.stdout.write(f"NAV records processed: {result['nav_records']}")

            if user is not None:
                sync_result = AMFIService.sync_user_nav_from_master(user)
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Legacy family sync completed: {sync_result['nav_records']} latest NAV records."
                    )
                )
            return

        self.stdout.write(
            self.style.NOTICE("Downloading latest AMFI NAV data into the shared master...")
        )

        try:
            result = AMFIService.import_latest_master_navs()
        except Exception as exc:
            raise CommandError(f"AMFI master import failed: {exc}")

        self.stdout.write(self.style.SUCCESS("AMFI master NAV import completed."))
        self.stdout.write(f"Schemes processed: {result['schemes']}")
        self.stdout.write(f"NAV records processed: {result['nav_records']}")

        if user is not None:
            sync_result = AMFIService.sync_user_nav_from_master(user)
            self.stdout.write(
                self.style.SUCCESS(
                    f"Legacy family sync completed: {sync_result['nav_records']} latest NAV records."
                )
            )
