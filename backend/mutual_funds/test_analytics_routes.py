from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import SimpleTestCase, TestCase
from django.urls import Resolver404, resolve
from rest_framework.test import APIRequestFactory, force_authenticate

from mutual_funds.models import AMFIMasterNAV, AMFIMasterScheme
from mutual_funds.sebi_views import sebi_fund_nav_history


class MutualFundAnalyticsRouteTests(SimpleTestCase):
    def test_analytics_endpoints_use_the_dedicated_namespace(self):
        expected_routes = {
            "/api/mutual-fund-analytics/overview/": "mutual-fund-analytics-overview",
            "/api/mutual-fund-analytics/research-report/": "mutual-fund-research-report",
            "/api/mutual-fund-analytics/research-universes/": "mutual-fund-research-universes",
            "/api/mutual-fund-analytics/india-market-feed/": "india-mutual-fund-market-feed",
            "/api/mutual-fund-analytics/api-usage/": "indianapi-usage-status",
            "/api/mutual-fund-analytics/refresh/": "mutual-fund-analytics-refresh",
            "/api/mutual-fund-analytics/sebi/summary/": "sebi-mf-universe-summary",
            "/api/mutual-fund-analytics/sebi/rankings/": "sebi-mf-universe-rankings",
            "/api/mutual-fund-analytics/sebi/funds/100001/history/": "sebi-mf-fund-nav-history",
            "/api/mutual-fund-analytics/sebi/export/": "sebi-mf-analytics-export",
            "/api/mutual-fund-analytics/sebi/charts/category-risk/": "sebi-mf-category-risk-chart",
            "/api/mutual-fund-analytics/sebi/charts/top-q1/": "sebi-mf-top-q1-chart",
        }

        for route, expected_name in expected_routes.items():
            with self.subTest(route=route):
                self.assertEqual(resolve(route).url_name, expected_name)

    def test_watch_list_namespace_does_not_expose_mutual_fund_analytics(self):
        old_analytics_routes = (
            "/api/watch-list/analytics/overview/",
            "/api/watch-list/analytics/research-report/",
            "/api/watch-list/analytics/india-market-feed/",
            "/api/watch-list/analytics/api-usage/",
            "/api/watch-list/analytics/refresh/",
        )

        for route in old_analytics_routes:
            with self.subTest(route=route):
                with self.assertRaises(Resolver404):
                    resolve(route)

    def test_existing_watch_list_routes_remain_available(self):
        self.assertEqual(
            resolve("/api/watch-list/products/").url_name,
            "watch-list-products",
        )


class SEBIFundNAVHistoryAPITests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="mf-history-test", password="test-password")
        self.scheme = AMFIMasterScheme.objects.create(
            scheme_code="100001",
            scheme_name="Example Multi Cap Fund - Direct Plan - Growth",
            plan="Direct Plan",
            option="Growth",
            level_3_category="Multi Cap",
            is_growth_plan=True,
            sebi_universe_active=True,
        )

    def test_history_is_positive_and_deduplicated_by_nav_date(self):
        AMFIMasterNAV.objects.create(
            scheme=self.scheme,
            date=date(2026, 11, 19),
            nav=Decimal("100.000000"),
            source="MFapi",
        )
        AMFIMasterNAV.objects.create(
            scheme=self.scheme,
            date=date(2026, 11, 19),
            nav=Decimal("101.000000"),
            source="AMFI",
        )
        AMFIMasterNAV.objects.create(
            scheme=self.scheme,
            date=date(2026, 11, 20),
            nav=Decimal("102.000000"),
            source="MFapi",
        )
        AMFIMasterNAV.objects.create(
            scheme=self.scheme,
            date=date(2026, 11, 21),
            nav=Decimal("0.000000"),
            source="AMFI",
        )

        request = APIRequestFactory().get("/api/mutual-fund-analytics/sebi/funds/100001/history/")
        force_authenticate(request, user=self.user)
        response = sebi_fund_nav_history(request, "100001")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["available"])
        self.assertEqual(response.data["history_count"], 2)
        self.assertEqual(
            response.data["history"],
            [
                {"date": "2026-11-19", "nav": 101.0},
                {"date": "2026-11-20", "nav": 102.0},
            ],
        )
        self.assertEqual(response.data["metrics"]["NAV_Latest"], 102.0)
