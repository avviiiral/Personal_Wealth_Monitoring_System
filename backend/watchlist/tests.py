from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APIClient

from investments.models import Asset, AssetCategory, PortfolioPosition, Transaction, TransactionType
from watchlist.models import InvestmentProduct, MutualFundProduct, PerformanceSnapshot, ProductType, WatchListEntry
from watchlist.services.ownership import OwnershipService
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

    def test_owned_status_filter_matches_owned_mutual_fund(self):
        product = InvestmentProduct.objects.create(
            product_type=ProductType.MUTUAL_FUND,
            name="Owned Fund",
            provider="Owned AMC",
            isin="INF000000001",
            identity_key="MUTUAL_FUND:ISIN:INF000000001",
            source="AMFI",
        )
        MutualFundProduct.objects.create(product=product, scheme_code="1001")
        asset = Asset.objects.create(
            owner=self.user,
            name="Owned Fund",
            category=AssetCategory.MUTUAL_FUND,
            isin="INF000000001",
            symbol="1001",
        )
        PortfolioPosition.objects.create(
            owner=self.user,
            family_name="My Family",
            portfolio="My Portfolio",
            asset=asset,
            quantity=10,
            current_value=1000,
        )

        owned = self.client.get("/api/watch-list/products/?product_type=MUTUAL_FUND&status=OWNED&page_size=50")
        universal = self.client.get("/api/watch-list/products/?product_type=MUTUAL_FUND&status=UNIVERSAL&page_size=50")

        self.assertEqual(owned.status_code, 200)
        self.assertEqual(owned.data["count"], 1)
        self.assertEqual(owned.data["results"][0]["name"], "Owned Fund")
        self.assertEqual(universal.status_code, 200)
        self.assertEqual(universal.data["count"], 0)

    def test_watch_list_filter_options(self):
        InvestmentProduct.objects.create(
            product_type=ProductType.MUTUAL_FUND,
            name="Fund A",
            provider="AMC Alpha",
            category="Equity",
            identity_key="MUTUAL_FUND:SCHEME:A",
        )
        InvestmentProduct.objects.create(
            product_type=ProductType.MUTUAL_FUND,
            name="Fund B",
            provider="AMC Beta",
            category="Debt",
            identity_key="MUTUAL_FUND:SCHEME:B",
        )
        InvestmentProduct.objects.create(
            product_type=ProductType.PMS,
            name="PMS A",
            provider="PMS Provider",
            category="Equity",
            identity_key="PMS:SCHEME:A",
        )

        response = self.client.get("/api/watch-list/filters/?product_type=MUTUAL_FUND")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["providers"], ["AMC Alpha", "AMC Beta"])
        self.assertEqual(response.data["categories"], ["Debt", "Equity"])

    def test_bulk_ownership_enrichment(self):
        product = InvestmentProduct.objects.create(
            product_type=ProductType.MUTUAL_FUND,
            name="Bulk Owned Fund",
            provider="Bulk AMC",
            isin="INF000000099",
            identity_key="MUTUAL_FUND:ISIN:INF000000099",
            source="AMFI",
        )
        MutualFundProduct.objects.create(product=product, scheme_code="1099")
        asset = Asset.objects.create(
            owner=self.user,
            name="Bulk Owned Fund",
            category=AssetCategory.MUTUAL_FUND,
            isin="INF000000099",
            symbol="1099",
        )
        PortfolioPosition.objects.create(
            owner=self.user,
            family_name="My Family",
            portfolio="My Portfolio",
            asset=asset,
            quantity=10,
            invested_value=900,
            current_value=1000,
            current_price=100,
        )
        Transaction.objects.create(
            owner=self.user,
            family_name="My Family",
            portfolio="My Portfolio",
            asset=asset,
            transaction_type=TransactionType.BUY,
            transaction_date=date(2025, 9, 15),
            quantity=10,
            price_per_unit=90,
            amount=900,
        )

        result = OwnershipService.bulk_enrich([product], self.user)

        self.assertEqual(result[product.id]["status"], "OWNED")
        self.assertEqual(len(result[product.id]["ownership"]), 1)
        self.assertEqual(result[product.id]["ownership"][0]["current_value"], Decimal("1000"))
        self.assertEqual(result[product.id]["owned_invested_value"], Decimal("900"))

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

    def test_history_parser_uses_current_amfi_eight_column_format(self):
        text = (
            "Scheme Code;NAV Name;Plan;Option;ISIN Div Payout/ISIN Growth;ISIN Div Reinvestment;"
            "Net Asset Value;Date\n"
            "152073;360 ONE Balanced Hybrid Fund;Direct Plan;IDCW Option;INF579M01AZ6;-;13.6769;31-Aug-2026\n"
        )
        records = AMFIPerformanceService.parse_history(text)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["scheme_code"], "152073")
        self.assertEqual(records[0]["name"], "360 ONE Balanced Hybrid Fund")
        self.assertEqual(records[0]["plan"], "Direct Plan")
        self.assertEqual(records[0]["option"], "IDCW Option")
        self.assertEqual(records[0]["isin"], "INF579M01AZ6")
        self.assertEqual(records[0]["nav"], Decimal("13.6769"))
        self.assertEqual(records[0]["date"], date(2026, 8, 31))

    def test_history_parser_uses_legacy_eight_column_format(self):
        text = (
            "Scheme Code;Scheme Name;ISIN Div Payout/ISIN Growth;ISIN Div Reinvestment;"
            "Net Asset Value;Repurchase Price;Sale Price;Date\n"
            "1;Test Fund;INF000000001;-;100.25;;;15-Sep-2026\n"
        )
        records = AMFIPerformanceService.parse_history(text)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["scheme_code"], "1")
        self.assertEqual(records[0]["nav"], Decimal("100.25"))

    @patch("watchlist.services.performance.requests.get")
    def test_download_history_requests_text_report(self, get):
        get.return_value.raise_for_status.return_value = None
        get.return_value.text = ""
        start = date(2026, 8, 1)
        end = date(2026, 8, 15)

        AMFIPerformanceService.download_history(start, end)

        get.assert_called_once_with(
            AMFIPerformanceService.HISTORY_URL,
            params={"tp": "1", "frmdt": "01-Aug-2026", "todt": "15-Aug-2026"},
            headers={"User-Agent": "PWMS-WatchList/1.0"},
            timeout=120,
        )

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

    def test_amfi_parser_extracts_category_header_separately_from_provider(self):
        feed = (
            "Open Ended Schemes (Overnight Fund)\n"
            "Provider One\n"
            "1;INF000000001;-;Generic Overnight Fund;Direct Plan;Growth;100.25;15-Sep-2026\n"
            "Open Ended Schemes (Liquid Fund)\n"
            "Provider Two\n"
            "2;INF000000002;-;Another Liquid Fund;Regular Plan;IDCW;50.10;15-Sep-2026\n"
        )
        records = AMFIUniverseService.parse_latest_feed(feed)
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0]["provider"], "Provider One")
        self.assertEqual(records[0]["category"], "Overnight Fund")
        self.assertEqual(records[1]["provider"], "Provider Two")
        self.assertEqual(records[1]["category"], "Liquid Fund")

    def test_discovery_saves_category_from_amfi_feed(self):
        feed = (
            "Open Ended Schemes (Overnight Fund)\n"
            "Provider One\n"
            "1;INF000000001;-;Generic Overnight Fund;Direct Plan;Growth;100.25;15-Sep-2026\n"
        )
        with patch.object(AMFIUniverseService, "download_latest", return_value=feed):
            AMFIUniverseService.refresh()
        product = InvestmentProduct.objects.get(isin="INF000000001")
        self.assertEqual(product.category, "Overnight Fund")
        self.assertEqual(product.mutual_fund.fund_type, "Overnight Fund")

    def test_search_matches_category(self):
        product = InvestmentProduct.objects.create(
            product_type=ProductType.MUTUAL_FUND,
            name="Some Debt Fund",
            provider="Some AMC",
            category="Liquid Fund",
            isin="INF000000009",
            identity_key="MUTUAL_FUND:ISIN:INF000000009",
            source="AMFI",
        )
        MutualFundProduct.objects.create(product=product, scheme_code="9009")

        response = self.client.get("/api/watch-list/products/?product_type=MUTUAL_FUND&search=Liquid&page_size=50")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["id"], product.id)

    def test_toggle_watch_list_membership(self):
        product = InvestmentProduct.objects.create(
            product_type=ProductType.MUTUAL_FUND,
            name="Toggle Fund",
            identity_key="MUTUAL_FUND:SCHEME:TOGGLE",
            external_identifier="TOGGLE",
            is_active=True,
        )
        MutualFundProduct.objects.create(product=product, scheme_code="TOGGLE")

        add = self.client.post(f"/api/watch-list/products/{product.id}/toggle/")
        self.assertEqual(add.status_code, 200)
        self.assertTrue(add.data["is_watchlisted"])
        self.assertTrue(WatchListEntry.objects.filter(user=self.user, product=product).exists())

        detail = self.client.get(f"/api/watch-list/products/{product.id}/")
        self.assertTrue(detail.data["is_watchlisted"])

        remove = self.client.post(f"/api/watch-list/products/{product.id}/toggle/")
        self.assertEqual(remove.status_code, 200)
        self.assertFalse(remove.data["is_watchlisted"])
        self.assertFalse(WatchListEntry.objects.filter(user=self.user, product=product).exists())

    def test_watchlist_status_filter_only_returns_starred_products(self):
        starred = InvestmentProduct.objects.create(
            product_type=ProductType.MUTUAL_FUND,
            name="Starred Fund",
            identity_key="MUTUAL_FUND:SCHEME:STARRED",
            external_identifier="STARRED",
            is_active=True,
        )
        MutualFundProduct.objects.create(product=starred, scheme_code="STARRED")
        unstarred = InvestmentProduct.objects.create(
            product_type=ProductType.MUTUAL_FUND,
            name="Unstarred Fund",
            identity_key="MUTUAL_FUND:SCHEME:UNSTARRED",
            external_identifier="UNSTARRED",
            is_active=True,
        )
        MutualFundProduct.objects.create(product=unstarred, scheme_code="UNSTARRED")
        WatchListEntry.objects.create(user=self.user, product=starred)

        response = self.client.get("/api/watch-list/products/?product_type=MUTUAL_FUND&status=WATCHLIST&page_size=50")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["id"], starred.id)
