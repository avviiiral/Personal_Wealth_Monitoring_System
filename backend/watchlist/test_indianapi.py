from datetime import date
from decimal import Decimal
from unittest.mock import Mock, patch

from django.test import SimpleTestCase, TestCase, override_settings
from django.core.management import call_command
from django.contrib.auth import get_user_model
from rest_framework.test import APIRequestFactory, force_authenticate

from mutual_funds.models import AMFIMasterNAV, AMFIMasterScheme
from watchlist.indianapi_reports import india_mutual_fund_feed
from watchlist.research_reports import latest_mutual_fund_research_report
from watchlist.models import MutualFundResearchReport
from watchlist.services.indianapi import (
    IndianAPIClient,
    IndianAPIConfigurationError,
    IndianAPIError,
    IndianAPIQuotaExceeded,
    IndianAPIRateLimitError,
    normalize_mutual_fund_payload,
)


class IndianAPINormalizationTests(SimpleTestCase):
    def test_normalizes_documented_asset_class_and_category_payload(self):
        payload = {
            "Equity": {
                "Large Cap": [{
                    "fund_name": "Example Large Cap Fund",
                    "latest_nav": "125.50",
                    "percentage_change": "0.45",
                    "asset_size": "25000",
                    "1_month_return": "1.2",
                    "1_year_return": "12.5",
                    "star_rating": 4,
                }]
            }
        }
        rows = normalize_mutual_fund_payload(payload)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["fund_name"], "Example Large Cap Fund")
        self.assertEqual(rows[0]["category"], "Large Cap")
        self.assertEqual(rows[0]["latest_nav"], 125.5)
        self.assertEqual(rows[0]["returns"]["1Y"], 12.5)

    def test_rejects_unsupported_payload_shape(self):
        with self.assertRaises(IndianAPIError):
            normalize_mutual_fund_payload(["not", "an", "object"])


class IndianAPIClientTests(TestCase):
    @override_settings(INDIANAPI_API_KEY="", INDIANAPI_BASE_URL="https://stock.indianapi.in")
    def test_missing_key_disables_only_optional_provider(self):
        client = IndianAPIClient()
        self.assertFalse(client.is_configured)
        with self.assertRaises(IndianAPIConfigurationError):
            client.fetch_mutual_funds()

    @override_settings(
        INDIANAPI_API_KEY="test-secret",
        INDIANAPI_BASE_URL="https://stock.indianapi.in",
        INDIANAPI_TIMEOUT_SECONDS=7,
    )
    @patch("watchlist.services.indianapi.requests.get")
    def test_uses_server_side_api_key_header(self, mock_get):
        response = Mock()
        response.json.return_value = {"Equity": {"Large Cap": [{"fund_name": "Example Fund"}]}}
        response.raise_for_status.return_value = None
        mock_get.return_value = response

        payload = IndianAPIClient().fetch_mutual_funds()

        self.assertIn("Equity", payload)
        self.assertEqual(mock_get.call_args.args[0], "https://stock.indianapi.in/mutual_funds")
        self.assertEqual(mock_get.call_args.kwargs["headers"]["X-Api-Key"], "test-secret")
        self.assertEqual(mock_get.call_args.kwargs["timeout"], 7)

    @override_settings(INDIANAPI_API_KEY="test-secret", INDIANAPI_MONTHLY_REQUEST_BUDGET=5)
    @patch("watchlist.services.indianapi.requests.get")
    def test_429_exposes_retry_headers_and_does_not_retry(self, mock_get):
        response = Mock()
        response.status_code = 429
        response.headers = {
            "Retry-After": "60",
            "X-RateLimit-Remaining": "0",
            "X-RateLimit-Reset": "1795100000",
        }
        response.json.return_value = {"message": "API credits exhausted"}
        mock_get.return_value = response

        with self.assertRaises(IndianAPIRateLimitError) as context:
            IndianAPIClient().fetch_mutual_funds()

        self.assertEqual(context.exception.retry_after, "60")
        self.assertEqual(context.exception.remaining, "0")
        self.assertIn("API credits exhausted", str(context.exception))
        self.assertEqual(mock_get.call_count, 1)


class IndianAPIFeedTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="india-feed-test", password="test-password")
        self.factory = APIRequestFactory()

    @override_settings(INDIANAPI_API_KEY="")
    def test_feed_falls_back_to_keyless_amfi_master(self):
        scheme = AMFIMasterScheme.objects.create(
            scheme_code="999001",
            scheme_name="PWMS Test Fund",
            isin_growth="INF000000001",
            is_active=True,
        )
        AMFIMasterNAV.objects.create(
            scheme=scheme,
            date=date(2026, 11, 18),
            nav=Decimal("123.456700"),
            source="AMFI",
        )
        request = self.factory.get("/api/watch-list/analytics/india-market-feed/")
        force_authenticate(request, user=self.user)
        response = india_mutual_fund_feed(request)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["source"], "AMFI")
        self.assertFalse(response.data["provider_configured"])
        self.assertEqual(response.data["funds"][0]["latest_nav"], 123.4567)

    @override_settings(INDIANAPI_API_KEY="")
    def test_cached_indianapi_snapshot_remains_available_without_key(self):
        MutualFundResearchReport.objects.create(
            report_key="indianapi-india",
            report_date=date(2026, 11, 18),
            report_cycle="IndianAPI India mutual funds",
            payload={"funds": [{"fund_name": "Cached Fund", "returns": {}}]},
        )
        request = self.factory.get("/api/watch-list/analytics/india-market-feed/")
        force_authenticate(request, user=self.user)
        response = india_mutual_fund_feed(request)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["available"])
        self.assertFalse(response.data["provider_configured"])
        self.assertEqual(response.data["funds"][0]["fund_name"], "Cached Fund")

    @override_settings(INDIANAPI_API_KEY="")
    def test_indianapi_snapshot_does_not_replace_nexedge_report_endpoint(self):
        MutualFundResearchReport.objects.create(
            report_key="multi-cap",
            report_date=date(2026, 10, 30),
            report_cycle="October 2026",
            payload={"RAW": {"funds": [{"name": "Nexedge Fund"}]}, "P2P": {}, "ATTR": {}},
        )
        MutualFundResearchReport.objects.create(
            report_key="indianapi-india",
            report_date=date(2026, 11, 19),
            report_cycle="IndianAPI India mutual funds",
            payload={"funds": [{"fund_name": "IndianAPI Fund", "returns": {}}]},
        )
        request = self.factory.get("/api/watch-list/analytics/research-report/")
        force_authenticate(request, user=self.user)
        response = latest_mutual_fund_research_report(request)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["report_key"], "multi-cap")
        self.assertIn("RAW", response.data["payload"])

    @override_settings(INDIANAPI_API_KEY="")
    def test_sync_command_skips_cleanly_without_key(self):
        from io import StringIO
        output = StringIO()
        call_command("sync_indianapi_mutual_funds", stdout=output)
        self.assertIn("Skipped IndianAPI sync", output.getvalue())
        self.assertEqual(MutualFundResearchReport.objects.count(), 0)


    @override_settings(INDIANAPI_API_KEY="test-secret", INDIANAPI_MONTHLY_REQUEST_BUDGET=1)
    @patch("watchlist.services.indianapi.requests.get")
    def test_monthly_budget_blocks_request_before_network_call(self, mock_get):
        response = Mock()
        response.status_code = 200
        response.json.return_value = {"Equity": {"Large Cap": [{"fund_name": "Example Fund"}]}}
        response.raise_for_status.return_value = None
        mock_get.return_value = response

        client = IndianAPIClient()
        client.fetch_mutual_funds()

        from watchlist.models import IndianAPIRequestUsage
        usage = IndianAPIRequestUsage.objects.get()
        self.assertEqual(usage.request_count, 1)
        self.assertEqual(client.last_request_usage["limit"], 1)

        with self.assertRaises(IndianAPIQuotaExceeded):
            client.fetch_mutual_funds()

        self.assertEqual(mock_get.call_count, 1)
        usage.refresh_from_db()
        self.assertEqual(usage.request_count, 1)

    @override_settings(INDIANAPI_API_KEY="test-secret", INDIANAPI_MONTHLY_REQUEST_BUDGET=2)
    @patch("watchlist.services.indianapi.requests.get", side_effect=__import__("requests").Timeout("timeout"))
    def test_network_failure_still_consumes_reserved_budget(self, mock_get):
        from watchlist.models import IndianAPIRequestUsage

        with self.assertRaises(IndianAPIError):
            IndianAPIClient().fetch_mutual_funds()

        usage = IndianAPIRequestUsage.objects.get()
        self.assertEqual(usage.request_count, 1)
        self.assertEqual(mock_get.call_count, 1)

    @override_settings(INDIANAPI_API_KEY="test-secret")
    @patch(
        "watchlist.management.commands.sync_indianapi_mutual_funds.IndianAPIClient.fetch_mutual_funds",
        side_effect=IndianAPIRateLimitError(
            "IndianAPI returned HTTP 429 (rate limit or API credits exhausted).",
            retry_after="60",
            remaining="0",
        ),
    )
    def test_rate_limit_preserves_existing_snapshot(self, _mock_fetch):
        from django.core.management.base import CommandError
        existing = MutualFundResearchReport.objects.create(
            report_key="indianapi-india",
            report_date=date(2026, 11, 18),
            report_cycle="IndianAPI India mutual funds",
            payload={"funds": [{"fund_name": "Last Good Snapshot"}]},
        )
        with self.assertRaises(CommandError) as context:
            call_command("sync_indianapi_mutual_funds")
        existing.refresh_from_db()
        self.assertEqual(existing.payload["funds"][0]["fund_name"], "Last Good Snapshot")
        self.assertIn("previously cached snapshot", str(context.exception))

    @override_settings(INDIANAPI_API_KEY="test-secret")
    @patch(
        "watchlist.management.commands.sync_indianapi_mutual_funds.IndianAPIClient.fetch_mutual_funds",
        return_value={
            "Equity": {
                "Large Cap": [{
                    "fund_name": "Imported Fund",
                    "latest_nav": "10.25",
                    "1_year_return": "8.5",
                }]
            }
        },
    )
    def test_sync_command_persists_a_dated_snapshot(self, _mock_fetch):
        call_command("sync_indianapi_mutual_funds")
        report = MutualFundResearchReport.objects.get(report_key="indianapi-india")
        self.assertEqual(report.payload["funds"][0]["fund_name"], "Imported Fund")
        self.assertEqual(report.payload["funds"][0]["latest_nav"], 10.25)
