from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase

from users.models import FamilyGroup
from rest_framework.test import APIClient

from investments.models import Asset, AssetCategory, PortfolioPosition, Transaction, TransactionType
from watchlist.models import InvestmentProduct, MutualFundProduct, PerformanceSnapshot, ProductType, WatchListEntry
from watchlist.services.ownership import OwnershipService
from watchlist.services.performance import AMFIPerformanceService
from watchlist.services.universe import AMFIUniverseService
from watchlist.services.pms import APMIPMSDiscoveryService
from watchlist.services.benchmark import BenchmarkPerformanceService


class WatchListTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="watchlist-user", password="pw")
        family = FamilyGroup.objects.create(name="Watchlist Family")
        self.user.profile.family_groups.add(family)
        self.family = family
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def test_amfi_parser_is_data_driven(self):
        feed = "Header\nProvider One\n1;INF000000001;-;Generic Equity Fund;Direct Plan;Growth;100.25;15-Sep-2026\nProvider Two\n2;INF000000002;-;Another Fund;Regular Plan;IDCW;50.10;15-Sep-2026\n"
        records = AMFIUniverseService.parse_latest_feed(feed)
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0]["provider"], "Provider One")
        self.assertEqual(records[1]["provider"], "Provider Two")

    def test_amfi_parser_supports_six_column_legacy_format(self):
        feed = ("Scheme Code;ISIN Div Payout/ISIN Growth;ISIN Div Reinvestment;Scheme Name;Net Asset Value;Date\nProvider One\n1;INF000000001;-;Generic Equity Fund;100.25;15-Sep-2026\n")
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
        product = InvestmentProduct.objects.create(product_type=ProductType.MUTUAL_FUND, name="Test Fund", identity_key="MUTUAL_FUND:SCHEME:1", source="AMFI")
        MutualFundProduct.objects.create(product=product, scheme_code="1", latest_nav=Decimal("10"))
        PerformanceSnapshot.objects.create(product=product, date="2026-09-15", nav_or_value=Decimal("10"), source="AMFI")
        PerformanceSnapshot.objects.update_or_create(product=product, date="2026-09-15", source="AMFI", defaults={"nav_or_value": Decimal("11")})
        self.assertEqual(PerformanceSnapshot.objects.filter(product=product).count(), 1)
        self.assertEqual(PerformanceSnapshot.objects.get(product=product).nav_or_value, Decimal("11"))

    def test_api_pagination_and_filters(self):
        for index in range(3):
            product = InvestmentProduct.objects.create(product_type=ProductType.MUTUAL_FUND, name=f"Fund {index}", identity_key=f"MUTUAL_FUND:SCHEME:{index}", source="AMFI")
            MutualFundProduct.objects.create(product=product, scheme_code=str(index))
        response = self.client.get("/api/watch-list/products/?product_type=MUTUAL_FUND&page_size=2")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data["results"]), 2)
        self.assertEqual(response.data["count"], 3)

    def test_watch_list_uses_latest_snapshot_for_metrics_and_performance(self):
        product = InvestmentProduct.objects.create(product_type=ProductType.MUTUAL_FUND, name="Snapshot Fund", identity_key="MUTUAL_FUND:SCHEME:SNAPSHOT", source="AMFI")
        MutualFundProduct.objects.create(product=product, scheme_code="SNAPSHOT")
        PerformanceSnapshot.objects.create(product=product, date="2026-09-14", nav_or_value=Decimal("10"), return_1m=Decimal("1"), source="AMFI")
        PerformanceSnapshot.objects.create(product=product, date="2026-09-15", nav_or_value=Decimal("11"), return_1m=Decimal("2.5"), return_1y=Decimal("12.5"), cagr=Decimal("3.75"), source="AMFI")
        response = self.client.get("/api/watch-list/products/?product_type=MUTUAL_FUND&search=Snapshot%20Fund&page_size=5")
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
        product = InvestmentProduct.objects.create(product_type=ProductType.MUTUAL_FUND, name="Owned Fund", isin="INFOWNED", identity_key="MUTUAL_FUND:ISIN:INFOWNED", source="AMFI")
        MutualFundProduct.objects.create(product=product, scheme_code="OWNED")
        asset = Asset.objects.create(owner=self.user, family=self.family, name="Owned Asset", symbol="OWNED", isin="INFOWNED", category=AssetCategory.MUTUAL_FUND)
        PortfolioPosition.objects.create(owner=self.user, family=self.family, asset=asset, quantity=1, current_value=100)
        response = self.client.get("/api/watch-list/products/?product_type=MUTUAL_FUND&status=OWNED")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["name"], "Owned Fund")

    def test_universal_status_filter_excludes_owned_mutual_fund(self):
        product = InvestmentProduct.objects.create(product_type=ProductType.MUTUAL_FUND, name="Owned Fund", isin="INFOWNED", identity_key="MUTUAL_FUND:ISIN:INFOWNED", source="AMFI")
        MutualFundProduct.objects.create(product=product, scheme_code="OWNED")
        asset = Asset.objects.create(owner=self.user, family=self.family, name="Owned Asset", symbol="OWNED", isin="INFOWNED", category=AssetCategory.MUTUAL_FUND)
        PortfolioPosition.objects.create(owner=self.user, family=self.family, asset=asset, quantity=1, current_value=100)
        response = self.client.get("/api/watch-list/products/?product_type=MUTUAL_FUND&status=UNIVERSAL")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 0)

    def test_watchlist_toggle(self):
        product = InvestmentProduct.objects.create(product_type=ProductType.MUTUAL_FUND, name="Toggle Fund", identity_key="MUTUAL_FUND:SCHEME:TOGGLE", source="AMFI")
        response = self.client.post(f"/api/watch-list/products/{product.id}/toggle/")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["is_watchlisted"])
        self.assertEqual(WatchListEntry.objects.filter(user=self.user, product=product).count(), 1)
        response = self.client.post(f"/api/watch-list/products/{product.id}/toggle/")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data["is_watchlisted"])
        self.assertFalse(WatchListEntry.objects.filter(user=self.user, product=product).exists())

    def test_amfi_performance_service_updates_snapshot(self):
        product = InvestmentProduct.objects.create(product_type=ProductType.MUTUAL_FUND, name="Performance Fund", isin="INFPERF", external_identifier="1", identity_key="MUTUAL_FUND:ISIN:INFPERF", source="AMFI")
        MutualFundProduct.objects.create(product=product, scheme_code="PERF")
        feed = "Scheme Code;ISIN Div Payout/ISIN Growth;ISIN Div Reinvestment;Scheme Name;Net Asset Value;Date\n1;INFPERF;-;Performance Fund;100;15-Sep-2026\n"
        with patch("watchlist.services.performance.requests.get") as mocked_get:
            mocked_get.return_value.text = feed
            mocked_get.return_value.raise_for_status.return_value = None
            AMFIPerformanceService.refresh()
        self.assertTrue(PerformanceSnapshot.objects.filter(product=product, source="AMFI").exists())


class BenchmarkPerformanceTests(TestCase):
    def test_bse500_csv_validation(self):
        csv_text = "Date,Close\n2021-01-01,100.0\n2021-01-04,101.5\n"
        points = BenchmarkPerformanceService._load_bse_tri_csv(csv_text)
        self.assertEqual(points[0]["date"], "2021-01-01")
        self.assertEqual(points[-1]["value"], 101.5)

    def test_bse500_rejects_duplicate_dates(self):
        csv_text = "Date,Close\n2021-01-01,100.0\n2021-01-01,101.5\n"
        with self.assertRaises(ValueError):
            BenchmarkPerformanceService._load_bse_tri_csv(csv_text)

    def test_bse500_rejects_non_positive_values(self):
        with self.assertRaises(ValueError):
            BenchmarkPerformanceService._load_bse_tri_csv("Date,Close\n2021-01-01,0\n")

    def test_bse500_rejects_missing_values(self):
        with self.assertRaises(ValueError):
            BenchmarkPerformanceService._load_bse_tri_csv("Date,Close\n2021-01-01,\n")

    @patch("watchlist.services.benchmark.requests.get")
    def test_bse500_fetches_bse500t_automatically(self, mocked_get):
        mocked_get.return_value.text = (
            "Index Name,Date,Open,High,Low,Close\n"
            "BSE500T,01/01/2021,100,101,99,100\n"
            "BSE500T,04/01/2021,100,102,99,101\n"
        )
        mocked_get.return_value.raise_for_status.return_value = None
        points = BenchmarkPerformanceService._fetch_bse_tri_points(
            date(2021, 1, 1), date(2021, 1, 4)
        )
        mocked_get.assert_called_once()
        self.assertEqual(
            mocked_get.call_args.kwargs["params"]["strIndex"], "BSE500T"
        )
        self.assertEqual(points[-1]["value"], 101.0)

    def test_bse500_insufficient_history_returns_unavailable(self):
        product = InvestmentProduct.objects.create(
            product_type=ProductType.MUTUAL_FUND,
            name="BSE TRI Fund",
            identity_key="MUTUAL_FUND:SCHEME:BSETRI",
            source="TEST",
        )
        MutualFundProduct.objects.create(product=product, scheme_code="BSETRI", benchmark="BSE 500")
        csv_text = "Date,Close\n2026-09-15,100.0\n2026-09-16,101.0\n"
        with patch.object(BenchmarkPerformanceService, "_bse_series", return_value=[]):
            result = BenchmarkPerformanceService.calculate(product)
        self.assertFalse(result["available"])

    def test_nifty50_regression_uses_existing_yahoo_path(self):
        product = InvestmentProduct.objects.create(
            product_type=ProductType.MUTUAL_FUND,
            name="Nifty Fund",
            identity_key="MUTUAL_FUND:SCHEME:NIFTY",
            source="TEST",
        )
        MutualFundProduct.objects.create(product=product, scheme_code="NIFTY", benchmark="Nifty 50")
        points = [
            {"date": "2021-01-01", "value": 100.0},
            {"date": "2026-01-01", "value": 150.0},
        ]
        with patch.object(BenchmarkPerformanceService, "_series", return_value=points):
            result = BenchmarkPerformanceService.calculate(product)
        self.assertTrue(result["available"])
        self.assertEqual(result["benchmark"], "Nifty 50")
        self.assertIsNotNone(result["benchmark_metrics"]["5Y"])

    def test_benchmark_api_response_for_bse500(self):
        product = InvestmentProduct.objects.create(
            product_type=ProductType.MUTUAL_FUND,
            name="API BSE TRI Fund",
            identity_key="MUTUAL_FUND:SCHEME:API-BSETRI",
            source="TEST",
        )
        MutualFundProduct.objects.create(product=product, scheme_code="API-BSETRI", benchmark="BSE 500")
        with patch.object(
            BenchmarkPerformanceService,
            "_bse_series",
            return_value=[
                {"date": "2021-01-01", "value": 100.0},
                {"date": "2026-01-01", "value": 150.0},
            ],
        ):
            response = self.client.get(f"/api/watch-list/products/{product.id}/benchmark-performance/?period=1Y")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["available"])
        self.assertEqual(response.data["benchmark"], "BSE 500")


class APMIPMSDiscoveryTests(TestCase):
    SAMPLE_HTML = '<table><tr><th>PMS Provider Name</th><th>IA Name</th><th>AUM (in INR Cr.)</th><th>1 Month</th><th>3 Months</th><th>6 Months</th><th>1 Year</th><th>2 Years</th><th>3 Years</th><th>4 Years</th><th>5 Years</th><th>Since Inception</th></tr><tr><td>ICICI Prudential Asset Management Company Ltd</td><td><a href="IaInsight.htm?IAID=2595">ICICI Prudential PMS Small and Midcap FPI Strategy</a></td><td>₹75.38</td><td>NA</td><td>NA</td><td>NA</td><td>NA</td><td>NA</td><td>NA</td><td>NA</td><td>NA</td><td>0.06</td></tr></table>'

    def test_apmi_parser_reads_html_rows(self):
        records = APMIPMSDiscoveryService._records(self.SAMPLE_HTML)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["iaid"], "2595")
        self.assertEqual(records[0]["aum"], Decimal("75.38"))
        self.assertEqual(records[0]["performance"]["si"], Decimal("0.06"))

    @patch("watchlist.services.pms.requests.get")
    def test_apmi_refresh_upserts_product_and_snapshot(self, mocked_get):
        mocked_get.return_value.text = self.SAMPLE_HTML
        mocked_get.return_value.raise_for_status.return_value = None
        result = APMIPMSDiscoveryService.refresh()
        self.assertEqual(result["discovered"], 1)
        product = InvestmentProduct.objects.get(product_type=ProductType.PMS)
        self.assertEqual(product.external_identifier, "2595")
        self.assertEqual(product.pms.aum, Decimal("75.38"))
        self.assertEqual(PerformanceSnapshot.objects.get(product=product, source="APMI").return_since_inception, Decimal("0.06"))

    @patch("watchlist.services.pms.requests.get")
    def test_apmi_refresh_is_idempotent(self, mocked_get):
        mocked_get.return_value.text = self.SAMPLE_HTML
        mocked_get.return_value.raise_for_status.return_value = None
        APMIPMSDiscoveryService.refresh()
        APMIPMSDiscoveryService.refresh()
        self.assertEqual(InvestmentProduct.objects.filter(product_type=ProductType.PMS).count(), 1)
        self.assertEqual(PerformanceSnapshot.objects.filter(source="APMI").count(), 1)
