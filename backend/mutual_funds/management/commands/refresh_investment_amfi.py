from django.core.management.base import BaseCommand, CommandError

from mutual_funds.services.investment_amfi import InvestmentAMFIService


class Command(BaseCommand):
    help = "Refresh latest AMFI NAVs for investment ISINs and backfill missing MIS history."

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
                from portfolio.mis_history_prefetch import MISHistoryPrefetch
                result["history"] = MISHistoryPrefetch.run_for_all_families()
            else:
                result = InvestmentAMFIService.refresh_for_investments_with_history()
        except Exception as exc:
            raise CommandError(f"Investment AMFI refresh failed: {exc}")

        self.stdout.write(
            self.style.SUCCESS("Investment-driven AMFI refresh completed.")
        )
        self.stdout.write(f"Requested ISINs: {result['requested_isins']}")
        self.stdout.write(f"Matched ISINs: {result['matched_isins']}")
        self.stdout.write(f"Schemes stored: {result['schemes']}")
        self.stdout.write(f"NAV records stored: {result['nav_records']}")
        history = result.get("history") or {}
        self.stdout.write(f"History schemes checked: {history.get('schemes', 0)}")
        self.stdout.write(f"History requests: {history.get('requests', 0)}")
        self.stdout.write(f"History failures: {history.get('failed', 0)}")
        if result["unmatched_isins"]:
            self.stdout.write(
                self.style.WARNING(
                    "Unmatched ISINs: " + ", ".join(result["unmatched_isins"])
                )
            )
