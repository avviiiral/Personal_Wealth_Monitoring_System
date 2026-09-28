from django.core.management.base import BaseCommand, CommandError

from mutual_funds.services.amfi import AMFIService


class Command(BaseCommand):
    help = "Refresh the shared global AMFI scheme and NAV master."

    def handle(self, *args, **options):
        self.stdout.write(
            self.style.NOTICE("Refreshing shared AMFI master data...")
        )
        try:
            result = AMFIService.import_latest_master_navs()
        except Exception as exc:
            raise CommandError(f"AMFI master refresh failed: {exc}")

        self.stdout.write(
            self.style.SUCCESS(
                "AMFI master refresh completed."
            )
        )
        self.stdout.write(f"Schemes processed: {result['schemes']}")
        self.stdout.write(f"NAV records processed: {result['nav_records']}")
