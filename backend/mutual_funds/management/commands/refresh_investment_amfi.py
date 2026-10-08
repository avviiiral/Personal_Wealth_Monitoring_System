from django.core.management.base import BaseCommand, CommandError

from mutual_funds.services.investment_amfi import InvestmentAMFIService


class Command(BaseCommand):
    help = "Refresh latest AMFI NAV data only for mutual-fund ISINs held in investments."

    def add_arguments(self, parser):
        parser.add_argument(
            "--isin",
            action="append",
            dest="isins",
            default=[],
            help="Refresh one or more investment ISINs explicitly. Repeatable.",
        )

    def handle(self, *args, **options):
        isins = options.get("isins") or []

        try:
            if isins:
                result = InvestmentAMFIService.refresh_for_isins(isins)
            else:
                result = InvestmentAMFIService.refresh_for_all_investments()
        except Exception as exc:
            raise CommandError(f"Investment AMFI refresh failed: {exc}")

        self.stdout.write(
            self.style.SUCCESS("Investment-driven AMFI refresh completed.")
        )
        self.stdout.write(f"Requested ISINs: {result['requested_isins']}")
        self.stdout.write(f"Matched ISINs: {result['matched_isins']}")
        self.stdout.write(f"Schemes stored: {result['schemes']}")
        self.stdout.write(f"NAV records stored: {result['nav_records']}")
        if result["unmatched_isins"]:
            self.stdout.write(
                self.style.WARNING(
                    "Unmatched ISINs: " + ", ".join(result["unmatched_isins"])
                )
            )
