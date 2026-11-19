from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIRequestFactory, force_authenticate

from mutual_funds.models import AMFIMasterNAV, AMFIMasterScheme
from watchlist.indianapi_reports import india_mutual_fund_feed, indianapi_usage_status
from watchlist.models import IndianAPIRequestUsage, MutualFundResearchReport
from watchlist.services.indianapi import IndianAPIClient


class IndianAPIUsageAndFeedTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="mf-analytics-reader", password="test-pass")
        self.factory = APIRequestFactory()

    @override_settings(INDIANAPI_MONTHLY_REQUEST_BUDGET=900, INDIANAPI_API_KEY="")
    def test_usage_endpoint_reports_persisted_ledger_and_clamps_budget(self):
        month = date.today().strftime("%Y-%m")
        IndianAPIRequestUsage.objects.create(
            month=month,
            request_count=7,
            refresh_status="SUCCESS",
        )
        request = self.factory.get("/api/watch-list/analytics/api-usage/")
        force_authenticate(request, user=self.user)
        response = indianapi_usage_status(request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["monthly_budget"], 400)
        self.assertEqual(response.data["requests_used"], 7)
        self.assertEqual(response.data["requests_remaining"], 393)
        self.assertEqual(response.data["last_refresh_status"], "SUCCESS")
        self.assertFalse(response.data["provider_configured"])

    @override_settings(INDIANAPI_MONTHLY_REQUEST_BUDGET=900)
    def test_indianapi_client_never_allows_more_than_400_requests(self):
        self.assertEqual(IndianAPIClient().monthly_budget, 400)

    @override_settings(INDIANAPI_API_KEY="test-secret")
    def test_cached_enrichment_nav_is_replaced_by_amfi_nav(self):
        scheme = AMFIMasterScheme.objects.create(
            scheme_code="100001",
            scheme_name="Exact Match Mutual Fund - Direct Growth",
            isin_growth="INF000000001",
            isin_dividend="",
        )
        AMFIMasterNAV.objects.create(
            scheme=scheme,
            date=date(2026, 11, 18),
            nav=Decimal("25.500000"),
        )
        MutualFundResearchReport.objects.create(
            report_key="indianapi-india",
            report_date=date(2026, 11, 19),
            report_cycle="IndianAPI India mutual funds",
            payload={
                "funds": [
                    {"fund_name": "Exact Match Mutual Fund - Direct Growth", "latest_nav": 999.0},
                    {"fund_name": "No Exact Match Fund", "latest_nav": 88.0},
                ],
            },
        )
        request = self.factory.get("/api/watch-list/analytics/india-market-feed/")
        force_authenticate(request, user=self.user)
        response = india_mutual_fund_feed(request)

        self.assertEqual(response.status_code, 200)
        rows = {row["fund_name"]: row for row in response.data["funds"]}
        self.assertEqual(rows["Exact Match Mutual Fund - Direct Growth"]["latest_nav"], 25.5)
        self.assertEqual(rows["Exact Match Mutual Fund - Direct Growth"]["scheme_code"], "100001")
        self.assertIsNone(rows["No Exact Match Fund"]["latest_nav"])
