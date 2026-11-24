from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from mf_intelligence.services import cap_list


class Command(BaseCommand):
    help = (
        "Load AMFI's Large/Mid/Small cap list used by Mutual Fund Intelligence. "
        "Without --file the newest workbook is downloaded from portal.amfiindia.com."
    )

    def add_arguments(self, parser):
        parser.add_argument("--file", help="Path to a downloaded AverageMarketCapitalization*.xlsx workbook.")
        parser.add_argument("--force", action="store_true", help="Re-download even if the latest period is stored.")

    def handle(self, *args, **options):
        if options["file"]:
            path = Path(options["file"])
            if not path.is_file():
                raise CommandError(f"File not found: {path}")
            period_end, rows = cap_list.parse_amfi_workbook(path.read_bytes())
            if not rows:
                raise CommandError("No ISIN rows were found in the workbook.")
            if period_end is None:
                raise CommandError("Could not read the period end date from the workbook title row.")
            count = cap_list.store_amfi_rows(rows, period_end)
            self.stdout.write(self.style.SUCCESS(f"Imported {count} classifications for period ending {period_end}."))
            return
        result = cap_list.refresh_amfi_classification(force=options["force"])
        if result.get("status") == "unavailable":
            raise CommandError("The AMFI workbook could not be downloaded. Pass --file with a downloaded copy.")
        self.stdout.write(self.style.SUCCESS(str(result)))
