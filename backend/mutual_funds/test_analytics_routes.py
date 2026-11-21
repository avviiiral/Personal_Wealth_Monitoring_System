from django.test import SimpleTestCase
from django.urls import Resolver404, resolve


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
