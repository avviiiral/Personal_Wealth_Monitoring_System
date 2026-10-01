from django.core.management.base import BaseCommand

from portfolio.mis_report_service import MISReportService


class Command(BaseCommand):
    help = "Refresh Yahoo Finance reference prices used by the MIS Notes sheet."

    def handle(self, *args, **options):
        result = MISReportService.refresh_reference_prices()
        self.stdout.write(
            self.style.SUCCESS(
                "MIS reference price refresh completed: "
                f"{result.get('refreshed', 0)}/{result.get('references', 0)} "
                f"references refreshed, {result.get('failed', 0)} failed, "
                f"{result.get('records', 0)} records saved."
            )
        )
