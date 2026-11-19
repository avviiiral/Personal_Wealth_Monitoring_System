from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from watchlist.models import IndianAPIRequestUsage, MutualFundResearchReport
from watchlist.services.indianapi import (
    IndianAPIClient,
    IndianAPIError,
    IndianAPIQuotaExceeded,
    IndianAPIRateLimitError,
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
        except IndianAPIQuotaExceeded as exc:
            raise CommandError(
                f"{exc}\n"
                "The keyless AMFI data feed remains available. No IndianAPI call was made."
            ) from exc
        except IndianAPIRateLimitError as exc:
            raise CommandError(
                f"{exc}\n"
                "Next step: inspect the IndianAPI account dashboard. If credits are exhausted, "
                "wait for renewal or upgrade; if the plan's short-window rate limit was hit, "
                "wait for the indicated reset before running this command once again. "
                "PWMS remains usable with AMFI and any previously cached snapshot."
            ) from exc
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

        # Read the persisted ledger rather than relying on client instance state.
        # This keeps reporting accurate if the fetch method is wrapped or mocked,
        # and makes the database the single source of truth for monthly usage.
        usage_month = timezone.localdate(now).strftime("%Y-%m")
        usage_record = IndianAPIRequestUsage.objects.filter(month=usage_month).only(
            "request_count"
        ).first()
        used = usage_record.request_count if usage_record else 0
        limit = client.monthly_budget
        remaining = max(0, limit - used)
        month_label = timezone.localdate(now).strftime("%B %Y")

        self.stdout.write(self.style.SUCCESS(
            f"{action} IndianAPI snapshot ({report.report_date}): {len(funds)} funds. "
            f"Monthly API usage: {used}/{limit} requests for {month_label} "
            f"({remaining} remaining in PWMS budget)."
        ))
