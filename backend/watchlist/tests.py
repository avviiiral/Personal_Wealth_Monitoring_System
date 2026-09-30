from datetime import date
from decimal import Decimal
from unittest.mock import patch
from datetime import timedelta

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from mutual_funds.models import AMFIMasterNAV, AMFIMasterScheme
from mutual_funds.services.amfi import AMFIService
from users.models import FamilyGroup
from rest_framework.test import APIClient

from investments.models import Asset, AssetCategory, PortfolioPosition, Transaction, TransactionType
from watchlist.models import BenchmarkMasterPoint, InvestmentProduct, MutualFundProduct, PMSProduct, PerformanceSnapshot, ProductType, WatchListEntry
from watchlist.services.ownership import OwnershipService
from watchlist.services.performance import AMFIPerformanceService
from watchlist.services.universe import AMFIUniverseService
from watchlist.services.pms import APMIPMSDiscoveryService
from watchlist.services.benchmark import BenchmarkPerformanceService
from watchlist.services.amfi_history import WatchListAMFIHistoryService


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
            PerformanceSnapshot.objects.create(
                product=product,
                date="2026-09-15",
                nav_or_value=Decimal("10"),
                return_1m=Decimal(str(index + 1)),
                source="AMFI",
            )
        response = self.client.get("/api/watch-list/products/?product_type=MUTUAL_FUND&page_size=2")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data["results"]), 2)
        self.assertEqual(response.data["count"], 3)

    def test_api_hides_products_with_no_displayable_values(self):
        visible = InvestmentProduct.objects.create(
            product_type=ProductType.MUTUAL_FUND,
            name="Visible Fund",
            identity_key="MUTUAL_FUND:SCHEME:VISIBLE",
            source="AMFI",
        )
        MutualFundProduct.objects.create(
            product=visible,
            scheme_code="VISIBLE",
            aum=Decimal("100"),
        )

        hidden = InvestmentProduct.objects.create(
            product_type=ProductType.MUTUAL_FUND,
            name="Empty Fund",
            identity_key="MUTUAL_FUND:SCHEME:EMPTY",
            source="AMFI",
        )
        MutualFundProduct.objects.create(product=hidden, scheme_code="EMPTY")
        PerformanceSnapshot.objects.create(
            product=hidden,
            date="2026-09-15",
            nav_or_value=Decimal("10"),
            source="AMFI",
        )

        response = self.client.get(
            "/api/watch-list/products/?product_type=MUTUAL_FUND&page_size=100"
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["name"], "Visible Fund")

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

    @patch("watchlist.views.prepare_mutual_fund_watchlist_history")
    def test_watchlist_toggle_prepares_new_mutual_fund_history(self, mocked_prepare):
        product = InvestmentProduct.objects.create(
            product_type=ProductType.MUTUAL_FUND,
            name="Auto History Fund",
            identity_key="MUTUAL_FUND:SCHEME:AUTO-HISTORY",
            source="AMFI",
        )
        MutualFundProduct.objects.create(
            product=product,
            scheme_code="152075",
        )

        response = self.client.post(
            f"/api/watch-list/products/{product.id}/toggle/"
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["is_watchlisted"])
        mocked_prepare.assert_called_once_with(product)

    @patch("watchlist.views.prepare_mutual_fund_watchlist_history")
    def test_watchlist_toggle_does_not_prepare_pms_history(self, mocked_prepare):
        product = InvestmentProduct.objects.create(
            product_type=ProductType.PMS,
            name="PMS Auto History Test",
            identity_key="PMS:SCHEME:AUTO-HISTORY",
            source="APMI",
        )
        PMSProduct.objects.create(product=product)

        response = self.client.post(
            f"/api/watch-list/products/{product.id}/toggle/"
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["is_watchlisted"])
        mocked_prepare.assert_not_called()

    @patch.object(
        AMFIService,
        "import_historical_master_navs",
        return_value={"schemes": 1, "nav_records": 7},
    )
    def test_amfi_history_service_imports_missing_master_history(self, mocked_import):
        product = InvestmentProduct.objects.create(
            product_type=ProductType.MUTUAL_FUND,
            name="Coverage Fund",
            identity_key="MUTUAL_FUND:SCHEME:COVERAGE",
            source="AMFI",
        )
        MutualFundProduct.objects.create(product=product, scheme_code="COVERAGE")

        result = WatchListAMFIHistoryService.prepare_product(product)

        self.assertTrue(result["prepared"])
        self.assertTrue(result["downloaded"])
        self.assertEqual(result["scheme_code"], "COVERAGE")
        self.assertEqual(result["nav_records"], 7)
        mocked_import.assert_called_once()
        self.assertEqual(
            mocked_import.call_args.kwargs["scheme_codes"],
            {"COVERAGE"},
        )

    @patch("watchlist.views.prepare_mutual_fund_watchlist_history")
    def test_watchlist_bulk_add_prepares_only_new_mutual_funds(self, mocked_prepare):
        mutual_fund = InvestmentProduct.objects.create(
            product_type=ProductType.MUTUAL_FUND,
            name="Bulk MF",
            identity_key="MUTUAL_FUND:SCHEME:BULK-MF",
            source="AMFI",
        )
        MutualFundProduct.objects.create(product=mutual_fund, scheme_code="BULK-MF")
        pms = InvestmentProduct.objects.create(
            product_type=ProductType.PMS,
            name="Bulk PMS",
            identity_key="PMS:SCHEME:BULK-PMS",
            source="APMI",
        )
        PMSProduct.objects.create(product=pms)

        response = self.client.post(
            reverse("watch-list-bulk-add"),
            {"product_ids": [mutual_fund.id, pms.id]},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["added"], 2)
        mocked_prepare.assert_called_once_with(mutual_fund)

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
        points = BenchmarkPerformanceService._load_bse_csv(csv_text)
        self.assertEqual(points[0]["date"], "2021-01-01")
        self.assertEqual(points[-1]["value"], 101.5)

    def test_bse500_rejects_duplicate_dates(self):
        csv_text = "Date,Close\n2021-01-01,100.0\n2021-01-01,101.5\n"
        with self.assertRaises(ValueError):
            BenchmarkPerformanceService._load_bse_csv(csv_text)

    def test_bse500_rejects_non_positive_values(self):
        with self.assertRaises(ValueError):
            BenchmarkPerformanceService._load_bse_csv("Date,Close\n2021-01-01,0\n")

    def test_bse500_rejects_missing_values(self):
        with self.assertRaises(ValueError):
            BenchmarkPerformanceService._load_bse_csv("Date,Close\n2021-01-01,\n")

    @patch("watchlist.services.benchmark.requests.get")
    def test_bse500_fetches_automatically(self, mocked_get):
        mocked_get.return_value.text = (
            "Index Name,Date,Open,High,Low,Close\n"
            "BSE500,01/01/2021,100,101,99,100\n"
            "BSE500,04/01/2021,100,102,99,101\n"
        )
        mocked_get.return_value.raise_for_status.return_value = None
        points = BenchmarkPerformanceService._fetch_bse_points(
            date(2021, 1, 1), date(2021, 1, 4)
        )
        mocked_get.assert_called_once()
        self.assertEqual(
            mocked_get.call_args.kwargs["params"]["strIndex"], "BSE500"
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

    def test_idcw_fund_returns_are_unavailable_without_distribution_data(self):
        product = InvestmentProduct.objects.create(
            product_type=ProductType.MUTUAL_FUND,
            name="IDCW Benchmark Test",
            isin="INF579M01AZ6",
            identity_key="MUTUAL_FUND:ISIN:INF579M01AZ6",
            source="AMFI",
        )
        MutualFundProduct.objects.create(
            product=product,
            scheme_code="152073",
            option="IDCW",
            benchmark="BSE 500",
        )
        PerformanceSnapshot.objects.create(
            product=product,
            date="2026-09-28",
            nav_or_value=Decimal("13.68"),
            source="AMFI",
        )
        with patch.object(
            BenchmarkPerformanceService,
            "_bse_series",
            return_value=[
                {"date": "2025-09-25", "value": 100.0},
                {"date": "2026-09-28", "value": 98.0},
            ],
        ):
            result = BenchmarkPerformanceService.calculate(product)

        self.assertTrue(result["available"])
        self.assertIsNone(result["fund_metrics"]["1Y"])
        self.assertIsNone(result["fund_return_details"]["1Y"])
        self.assertEqual(result["benchmark_metrics"]["1Y"], -2.0)

    def test_benchmark_long_periods_are_cumulative_not_cagr(self):
        points = [
            {"date": "2021-01-01", "value": 100.0},
            {"date": "2026-01-01", "value": 150.0},
        ]
        product = InvestmentProduct.objects.create(
            product_type=ProductType.MUTUAL_FUND,
            name="Benchmark Return Basis Test",
            identity_key="MUTUAL_FUND:SCHEME:BENCHMARK-BASIS",
            source="TEST",
        )
        MutualFundProduct.objects.create(
            product=product,
            scheme_code="BENCHMARK-BASIS",
            benchmark="Nifty 50",
        )
        with patch.object(
            BenchmarkPerformanceService,
            "_series",
            return_value=points,
        ):
            result = BenchmarkPerformanceService.calculate(product)

        self.assertEqual(result["benchmark_metrics"]["5Y"], 50.0)
        self.assertEqual(
            result["benchmark_return_details"]["5Y"]["method"],
            "Cumulative return",
        )
        self.assertNotIn("benchmark_cagr_3y", result)
        self.assertNotIn("benchmark_cagr_5y", result)

    def test_return_detail_exposes_exact_observations(self):
        points = [
            {"date": "2025-09-25", "value": 100.0},
            {"date": "2026-09-25", "value": 105.0},
            {"date": "2026-09-28", "value": 106.0},
        ]
        detail = BenchmarkPerformanceService._period_return_detail(points, 365)
        self.assertEqual(detail["start_date"], "2025-09-25")
        self.assertEqual(detail["end_date"], "2026-09-28")
        self.assertEqual(detail["start_value"], 105.0)
        self.assertEqual(detail["end_value"], 106.0)
        self.assertEqual(detail["method"], "Cumulative return")

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

    def test_chart_reads_shared_benchmark_master_without_network_fetch(self):
        product = InvestmentProduct.objects.create(
            product_type=ProductType.MUTUAL_FUND,
            name="Master Benchmark Read Test",
            identity_key="MUTUAL_FUND:SCHEME:MASTER-BENCHMARK-READ",
            source="TEST",
        )
        MutualFundProduct.objects.create(
            product=product,
            scheme_code="MASTER-BENCHMARK-READ",
            benchmark="Nifty 50",
        )
        AMFIMasterScheme.objects.create(
            scheme_code="MASTER-BENCHMARK-READ",
            scheme_name="Master Benchmark Read Test",
        )
        scheme = AMFIMasterScheme.objects.get(scheme_code="MASTER-BENCHMARK-READ")
        AMFIMasterNAV.objects.create(
            scheme=scheme,
            date="2025-01-02",
            nav=Decimal("100.00"),
            source="AMFI",
        )
        AMFIMasterNAV.objects.create(
            scheme=scheme,
            date="2026-09-28",
            nav=Decimal("120.00"),
            source="AMFI",
        )
        BenchmarkMasterPoint.objects.create(
            benchmark="Nifty 50",
            date="2025-01-02",
            value=Decimal("24000.00"),
            source="MASTER",
        )
        BenchmarkMasterPoint.objects.create(
            benchmark="Nifty 50",
            date="2026-09-28",
            value=Decimal("25000.00"),
            source="MASTER",
        )

        with patch.object(
            BenchmarkPerformanceService,
            "_series",
            side_effect=AssertionError("chart endpoint attempted a network fetch"),
        ):
            result = BenchmarkPerformanceService.calculate(product, "1Y")

        self.assertTrue(result["available"])
        self.assertGreaterEqual(len(result["chart"]["aligned_points"]), 2)

    def test_chart_uses_actual_values_and_on_or_before_benchmark_alignment(self):
        aligned = BenchmarkPerformanceService._aligned_chart_series(
            [
                {"date": "2026-09-15", "value": 120.50},
                {"date": "2026-09-16", "value": 121.25},
                {"date": "2026-09-17", "value": 122.00},
            ],
            [
                {"date": "2026-09-14", "value": 25000.0},
                {"date": "2026-09-16", "value": 25100.0},
                {"date": "2026-09-18", "value": 25300.0},
            ],
            365,
        )

        self.assertEqual(
            aligned["points"],
            [
                {"date": "2026-09-15", "product_value": 120.50, "benchmark_value": 25000.0},
                {"date": "2026-09-16", "product_value": 121.25, "benchmark_value": 25100.0},
                {"date": "2026-09-17", "product_value": 122.00, "benchmark_value": 25100.0},
            ],
        )

    def test_chart_never_uses_future_benchmark_value(self):
        aligned = BenchmarkPerformanceService._aligned_chart_series(
            [
                {"date": "2026-09-15", "value": 120.0},
                {"date": "2026-09-16", "value": 121.0},
            ],
            [
                {"date": "2026-09-16", "value": 25100.0},
                {"date": "2026-09-17", "value": 25200.0},
            ],
            31,
        )
        self.assertIsNone(aligned)

    def test_chart_supports_all_periods_for_pms_value_history(self):
        product = InvestmentProduct.objects.create(
            product_type=ProductType.PMS,
            name="PMS Historical Value",
            identity_key="PMS:TEST:PMS-HISTORY",
            source="APMI",
        )
        PMSProduct.objects.create(product=product, benchmark="Nifty 50")
        start = date(2021, 1, 1)
        PerformanceSnapshot.objects.bulk_create(
            [
                PerformanceSnapshot(
                    product=product,
                    date=start + timedelta(days=day),
                    nav_or_value=Decimal("100.00") + Decimal(day) / Decimal("10"),
                    source="APMI",
                )
                for day in range(0, 365 * 5 + 20, 30)
            ]
        )
        benchmark_points = [
            {"date": (start + timedelta(days=day)).isoformat(), "value": 15000.0 + day}
            for day in range(0, 365 * 5 + 21, 7)
        ]

        with patch.object(BenchmarkPerformanceService, "_benchmark_series", return_value=benchmark_points):
            for period in BenchmarkPerformanceService.PERIOD_DAYS:
                result = BenchmarkPerformanceService.calculate(product, period)
                self.assertTrue(result["available"])
                self.assertGreaterEqual(len(result["chart"]["aligned_points"]), 2)
                self.assertEqual(
                    result["chart"]["fund"][0]["value"],
                    result["chart"]["aligned_points"][0]["product_value"],
                )
                self.assertEqual(
                    result["chart"]["benchmark"][0]["value"],
                    result["chart"]["aligned_points"][0]["benchmark_value"],
                )

    def test_chart_uses_shared_amfi_master_nav_for_mutual_fund(self):
        product = InvestmentProduct.objects.create(
            product_type=ProductType.MUTUAL_FUND,
            name="Master NAV Chart Fund",
            identity_key="MUTUAL_FUND:SCHEME:MASTER-CHART",
            source="AMFI",
        )
        scheme = AMFIMasterScheme.objects.create(
            scheme_code="MASTER-CHART",
            scheme_name="Master NAV Chart Fund",
        )
        MutualFundProduct.objects.create(
            product=product,
            scheme_code="MASTER-CHART",
            benchmark="Nifty 50",
        )
        AMFIMasterNAV.objects.create(
            scheme=scheme,
            date="2026-09-15",
            nav=Decimal("100.25"),
            source="AMFI",
        )
        AMFIMasterNAV.objects.create(
            scheme=scheme,
            date="2026-09-16",
            nav=Decimal("101.25"),
            source="AMFI",
        )

        series = BenchmarkPerformanceService._fund_series(
            product, date(2026, 9, 1), date(2026, 9, 30)
        )
        self.assertEqual(
            series,
            [
                {"date": "2026-09-15", "value": 100.25},
                {"date": "2026-09-16", "value": 101.25},
            ],
        )



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
