from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APIClient

from watchlist.models import InvestmentProduct, MutualFundProduct, PerformanceSnapshot, ProductType
from watchlist.services.performance import AMFIPerformanceService
from watchlist.services.universe import AMFIUniverseService


class WatchListTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="watchlist-user", password="pw")
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def test_amfi_parser_is_data_driven(self):
        feed = "Header\nProvider One\n1;INF000000001;-;Generic Equity Fund;Direct Plan;Growth;100.25;15-Sep-2026\nProvider Two\n2;INF000000002;-;Another Fund;Regular Plan;IDCW;50.10;15-Sep-2026\n"
        records = AMFIUniverseService.parse_latest_feed(feed)
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0]["provider"], "Provider One")
        self.assertEqual(records[1]["provider"], "Provider Two")

    def test_amfi_parser_supports_six_column_legacy_format(self):
        feed = (
            "Scheme Code;ISIN Div Payout/ISIN Growth;ISIN Div Reinvestment;Scheme Name;Net Asset Value;Date\n"
            "Provider One\n"
            "1;INF000000001;-;Generic Equity Fund;100.25;15-Sep-2026\n"
        )
        records = AMFIUniverseService.parse_latest_feed(feed)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["scheme_code"], "1")
        self.assertEqual(records[0]["isin"], "INF000000001")
        self.assertEqual(records[0]["name"], "Generic Equity Fund")
        self.assertEqual(records[0]["provider"], "Provider One")
        self.assertIsNone(records[0]["plan"])
        self.assertIsNone(records[0]["option"])
        self.assertEqual(records[0]["nav"], Decimal("100.25"))
        self.assertEqual(records[0]["date"], date(2026, 9, 15))

    def test_identity_prefers_isin(self):
        identity = AMFIUniverseService.identity({"isin": "inf123", "scheme_code": "9"})
        self.assertEqual(identity, "MUTUAL_FUND:ISIN:INF123")

    def test_duplicate_snapshot_is_upserted(self):
        product = InvestmentProduct.objects.create(
            product_type=ProductType.MUTUAL_FUND,
            name="Test Fund",
            identity_key="MUTUAL_FUND:SCHEME:1",
            source="AMFI",
        )
        MutualFundProduct.objects.create(product=product, scheme_code="1", latest_nav=Decimal("10"))
        PerformanceSnapshot.objects.create(product=product, date="2026-09-15", nav_or_value=Decimal("10"), source="AMFI")
        PerformanceSnapshot.objects.update_or_create(
            product=product, date="2026-09-15", source="AMFI",
            defaults={"nav_or_value": Decimal("11")},
        )
        self.assertEqual(PerformanceSnapshot.objects.filter(product=product).count(), 1)
        self.assertEqual(PerformanceSnapshot.objects.get(product=product).nav_or_value, Decimal("11"))

    def test_api_pagination_and_filters(self):
        for index in range(3):
            product = InvestmentProduct.objects.create(
                product_type=ProductType.MUTUAL_FUND,
                name=f"Fund {index}",
                identity_key=f"MUTUAL_FUND:SCHEME:{index}",
                source="AMFI",
            )
            MutualFundProduct.objects.create(product=product, scheme_code=str(index))
        response = self.client.get("/api/watch-list/products/?product_type=MUTUAL_FUND&page_size=2")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data["results"]), 2)
        self.assertEqual(response.data["count"], 3)

    def test_watch_list_uses_latest_snapshot_for_metrics_and_performance(self):
        product = InvestmentProduct.objects.create(
            product_type=ProductType.MUTUAL_FUND,
            name="Snapshot Fund",
            identity_key="MUTUAL_FUND:SCHEME:SNAPSHOT",
            source="AMFI",
        )
        MutualFundProduct.objects.create(product=product, scheme_code="SNAPSHOT")
        PerformanceSnapshot.objects.create(
            product=product,
            date="2026-09-14",
            nav_or_value=Decimal("10"),
            return_1m=Decimal("1"),
            source="AMFI",
        )
        PerformanceSnapshot.objects.create(
            product=product,
            date="2026-09-15",
            nav_or_value=Decimal("11"),
            return_1m=Decimal("2.5"),
            return_1y=Decimal("12.5"),
            cagr=Decimal("3.75"),
            source="AMFI",
        )

        response = self.client.get(
            "/api/watch-list/products/?product_type=MUTUAL_FUND&search=Snapshot%20Fund&page_size=5"
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data["results"]), 1)
        result = response.data["results"][0]
        self.assertEqual(result["metrics"]["1M"], Decimal("2.5"))
        self.assertEqual(result["metrics"]["1Y"], Decimal("12.5"))
        self.assertEqual(result["metrics"]["CAGR"], Decimal("3.75"))
        self.assertEqual(len(result["performance"]), 1)
        self.assertEqual(result["performance"][0]["date"], "2026-09-15")
        self.assertEqual(Decimal(result["performance"][0]["nav_or_value"]), Decimal("11"))

    @patch("watchlist.services.universe.AMFIUniverseService.download_latest")
    def test_discovery_creates_products(self, download):
        download.return_value = "AMC\n1;INF000000001;-;Fund One;Direct;Growth;10.00;15-Sep-2026\n"
        with patch.object(AMFIPerformanceService, "refresh", return_value={"history_requests": 0}):
            result = AMFIUniverseService.refresh()
        self.assertEqual(result["discovered"], 1)
        self.assertTrue(InvestmentProduct.objects.filter(isin="INF000000001").exists())

    def test_performance_history_endpoint(self):
        product = InvestmentProduct.objects.create(
            product_type=ProductType.MUTUAL_FUND,
            name="History Fund",
            identity_key="MUTUAL_FUND:SCHEME:HISTORY",
        )
        PerformanceSnapshot.objects.create(product=product, date="2026-09-15", nav_or_value=Decimal("12"), source="AMFI")
        response = self.client.get(f"/api/watch-list/products/{product.id}/performance/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data["results"]), 1)
