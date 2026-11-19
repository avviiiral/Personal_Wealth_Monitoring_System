from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from watchlist.models import MutualFundResearchReport
from watchlist.services.indianapi import (
    IndianAPIClient,
    IndianAPIError,
    normalize_mutual_fund_payload,
)


class Command(BaseCommand):
    help = (
        "Fetch the optional IndianAPI mutual-fund dataset and store a dated snapshot. "
        "AMFI and the rest of PWMS do not require this API key."
    )

    def handle(self, *args, **options):
        client = IndianAPIClient()
        if not client.is_configured:
            self.stdout.write(self.style.WARNING(
                "Skipped IndianAPI sync: INDIANAPI_API_KEY is not configured. "
                "PWMS remains fully operational; use refresh_amfi_master for keyless AMFI data."
            ))
            return
        try:
            raw_payload = client.fetch_mutual_funds()
            funds = normalize_mutual_fund_payload(raw_payload)
        except IndianAPIError as exc:
            raise CommandError(str(exc)) from exc
        if not funds:
            raise CommandError("IndianAPI returned no mutual-fund records in the documented category structure.")

        now = timezone.now()
        report, created = MutualFundResearchReport.objects.update_or_create(
            report_key="indianapi-india",
            report_date=timezone.localdate(now),
            defaults={
                "report_cycle": "IndianAPI India mutual funds",
                "source_file": "IndianAPI /mutual_funds",
                "payload": {
                    "provider": "IndianAPI",
                    "retrieved_at": now.isoformat(),
                    "funds": funds,
                    "raw": raw_payload,
                },
            },
        )
        action = "Created" if created else "Updated"
        self.stdout.write(self.style.SUCCESS(
            f"{action} IndianAPI snapshot ({report.report_date}): {len(funds)} funds."
        ))
