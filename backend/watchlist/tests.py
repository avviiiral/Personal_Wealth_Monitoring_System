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
            "Scheme Code;ISIN Div Payout/ ISIN Growth;ISIN Div Reinvestment;Scheme Name;Net Asset Value;Date\n"
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

    def test_history_parser_uses_amfi_eight_column_format(self):
        text = (
            "Scheme Code;Scheme Name;ISIN Div Payout/ISIN Growth;ISIN Div Reinvestment;"
            "Net Asset Value;Repurchase Price;Sale Price;Date\n"
            "1;Test Fund;INF000000001;-;100.25;;;15-Sep-2026\n"
        )
        records = AMFIPerformanceService.parse_history(text)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["scheme_code"], "1")
        self.assertEqual(records[0]["nav"], Decimal("100.25"))

    def test_subtract_months_handles_month_end(self):
        self.assertEqual(AMFIPerformanceService._subtract_months(date(2026, 3, 31), 1), date(2026, 2, 28))

    @patch("watchlist.services.performance.AMFIPerformanceService.download_history")
    def test_refresh_calculates_period_returns_and_cagr(self, download):
        product = InvestmentProduct.objects.create(
            product_type=ProductType.MUTUAL_FUND,
            name="Performance Fund",
            identity_key="MUTUAL_FUND:SCHEME:123",
            external_identifier="123",
            is_active=True,
        )
        MutualFundProduct.objects.create(product=product, scheme_code="123", latest_nav=Decimal("150"), latest_nav_date="2026-09-15")
        PerformanceSnapshot.objects.create(product=product, date="2026-09-15", nav_or_value=Decimal("150"), source="AMFI")
        history = (
            "123;Performance Fund;INF000000001;-;100;;;15-Sep-2021\n"
            "123;Performance Fund;INF000000001;-;125;;;15-Sep-2025\n"
            "123;Performance Fund;INF000000001;-;140;;;15-Aug-2026\n"
        )
        download.return_value = history

        result = AMFIPerformanceService.refresh()

        self.assertEqual(result["history_requests"], 6)
        self.assertEqual(result["metrics_updated"], 1)
        latest = PerformanceSnapshot.objects.get(product=product, date="2026-09-15")
        self.assertEqual(latest.return_1m, Decimal("7.142857"))
        self.assertIsNotNone(latest.return_1y)
        self.assertIsNotNone(latest.return_5y)
        self.assertIsNotNone(latest.cagr)

    def test_refresh_does_not_fetch_history_when_metrics_are_complete(self):
        product = InvestmentProduct.objects.create(
            product_type=ProductType.MUTUAL_FUND,
            name="Complete Fund",
            identity_key="MUTUAL_FUND:SCHEME:COMPLETE",
            external_identifier="456",
            is_active=True,
        )
        MutualFundProduct.objects.create(product=product, scheme_code="456")
        defaults = {
            "return_1m": Decimal("1"), "return_3m": Decimal("2"), "return_6m": Decimal("3"),
            "return_1y": Decimal("4"), "return_3y": Decimal("5"), "return_5y": Decimal("6"),
            "cagr": Decimal("4"), "nav_or_value": Decimal("100"),
        }
        PerformanceSnapshot.objects.create(product=product, date="2026-09-15", source="AMFI", **defaults)
        with patch.object(AMFIPerformanceService, "download_history") as download:
            result = AMFIPerformanceService.refresh()
        download.assert_not_called()
        self.assertEqual(result["history_requests"], 0)
