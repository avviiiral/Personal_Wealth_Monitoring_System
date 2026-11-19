from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from watchlist.models import IndianAPIRequestUsage, MutualFundResearchReport


class IndianAPIQuotaReportingTests(TestCase):
    @override_settings(
        INDIANAPI_API_KEY="test-secret",
        INDIANAPI_MONTHLY_REQUEST_BUDGET=450,
    )
    @patch(
        "watchlist.management.commands.sync_indianapi_mutual_funds."
        "IndianAPIClient.fetch_mutual_funds",
        return_value={
            "Equity": {
                "Large Cap": [{"fund_name": "Reporting Test Fund"}],
            },
        },
    )
    def test_sync_reports_persisted_monthly_usage(self, _mock_fetch):
        # The mocked fetch bypasses the client's reservation method. Seed the
        # same persisted ledger that a real successful API attempt would create.
        month = timezone.localdate().strftime("%Y-%m")
        IndianAPIRequestUsage.objects.create(month=month, request_count=1)

        output = StringIO()
        call_command("sync_indianapi_mutual_funds", stdout=output)

        self.assertTrue(
            MutualFundResearchReport.objects.filter(
                report_key="indianapi-india"
            ).exists()
        )
        self.assertIn("Monthly API usage: 1/450 requests", output.getvalue())
        self.assertIn("(449 remaining in PWMS budget)", output.getvalue())
        self.assertNotIn("?/?", output.getvalue())
