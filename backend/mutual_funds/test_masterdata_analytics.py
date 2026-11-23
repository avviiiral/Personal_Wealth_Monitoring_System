from datetime import date
from decimal import Decimal

from django.test import TestCase

from mutual_funds.models import AMFIMasterNAV, AMFIMasterScheme
from mutual_funds.master_models import MFBenchmarkTRI, MFMarketCapClassification, MFMasterHolding
from mutual_funds.services.masterdata_analytics import calculate_scheme_masterdata_metrics


class MasterDataRatioEngineTests(TestCase):
    def setUp(self):
        self.scheme = AMFIMasterScheme.objects.create(
            scheme_code="990001", scheme_name="PWMS Ratio Test Fund",
            isin_growth="INF990A01001", plan="Direct Plan", option="Growth",
            is_growth_plan=True, sebi_universe_active=True,
        )
        self.days = [date(2024, 1, day) for day in (1, 2, 3, 4, 5)]
        fund = [Decimal("100"), Decimal("110"), Decimal("132"), Decimal("118.8"), Decimal("124.74")]
        bench = [Decimal("100"), Decimal("105"), Decimal("115.5"), Decimal("109.725"), Decimal("112.468125")]
        for day, nav in zip(self.days, fund):
            AMFIMasterNAV.objects.create(scheme=self.scheme, date=day, nav=nav, source="AMFI")
        for day, tri in zip(self.days, bench):
            MFBenchmarkTRI.objects.create(index_code="NIFTY_TEST_TRI", index_name="Nifty Test TRI", date=day, tri_value=tri, source="NSE_TEST")
        from mutual_funds.master_models import MFMasterData
        MFMasterData.objects.create(scheme=self.scheme, as_of_date=self.days[-1], benchmark_name="Nifty Test TRI")
        MFMasterHolding.objects.create(
            scheme=self.scheme, portfolio_date=self.days[-1], security_name="Reliance Industries",
            isin="INE002A01018", security_key="ISIN:INE002A01018", instrument_type="EQUITY",
            sector="Energy", quantity=Decimal("10"), market_value=Decimal("1000"),
            pct_of_nav=Decimal("70"), source="AMC_TEST",
        )
        MFMasterHolding.objects.create(
            scheme=self.scheme, portfolio_date=self.days[-1], security_name="Government Bond",
            isin="IN0020240001", security_key="ISIN:IN0020240001", instrument_type="BOND",
            rating="AAA", rating_agency="CRISIL", pct_of_nav=Decimal("20"),
            ytm_pct=Decimal("7.12"), source="AMC_TEST",
        )
        MFMasterHolding.objects.create(
            scheme=self.scheme, portfolio_date=self.days[-1], security_name="TREPS",
            security_key="NAME:TREPS", instrument_type="CASH", pct_of_nav=Decimal("10"),
            source="AMC_TEST",
        )
        MFMarketCapClassification.objects.create(
            isin="INE002A01018", company_name="Reliance Industries",
            market_cap_class="LARGE_CAP", effective_period="2024-H1", source="AMFI_TEST",
        )

    def test_holdings_allocation_sector_and_cap_split(self):
        result = calculate_scheme_masterdata_metrics(self.scheme, self.days[-1], ratio_window_years=0)
        values = result["values"]
        self.assertEqual(values["equity_pct"], Decimal("70.0000"))
        self.assertEqual(values["debt_pct"], Decimal("20.0000"))
        self.assertEqual(values["cash_pct"], Decimal("10.0000"))
        self.assertEqual(values["large_cap_pct"], Decimal("70.0000"))
        self.assertEqual(values["other_cap_pct"], Decimal("0.0000"))
        self.assertEqual(values["sector_allocation"]["Energy"], "70.0000")
        self.assertEqual(values["credit_rating_split"]["AAA"], "20.0000")
        self.assertEqual(values["top10_concentration_pct"], Decimal("100.0000"))

    def test_benchmark_relative_metrics_are_populated_from_tri(self):
        result = calculate_scheme_masterdata_metrics(
            self.scheme, self.days[-1], risk_free_rate=0.0, ratio_window_years=0,
        )
        values = result["values"]
        self.assertAlmostEqual(float(values["beta"]), 2.0, places=6)
        self.assertAlmostEqual(float(values["r_squared"]), 1.0, places=6)
        self.assertAlmostEqual(float(values["alpha_pct"]), 0.0, places=2)
        self.assertIsNotNone(values["tracking_error_pct"])

    def test_default_three_year_window_keeps_short_history_ratios_null(self):
        result = calculate_scheme_masterdata_metrics(self.scheme, self.days[-1])
        self.assertIsNone(result["values"].get("beta"))
        self.assertIn("INSUFFICIENT_BENCHMARK_HISTORY", result["data_quality_flags"])


    def test_market_cap_classification_is_selected_for_snapshot_half_year(self):
        MFMarketCapClassification.objects.create(
            isin="INE002A01018", company_name="Reliance Industries",
            market_cap_class="SMALL_CAP", effective_period="2024-H2", source="AMFI_TEST",
        )
        result = calculate_scheme_masterdata_metrics(self.scheme, self.days[-1], ratio_window_years=0)
        self.assertEqual(result["values"]["large_cap_pct"], Decimal("70.0000"))
        self.assertEqual(result["values"]["small_cap_pct"], Decimal("0.0000"))


    def test_rolling_three_year_cagr_is_exposed(self):
        observations = [
            (date(2020, 1, 1), Decimal("100")),
            (date(2021, 1, 1), Decimal("110")),
            (date(2022, 1, 1), Decimal("121")),
            (date(2023, 1, 1), Decimal("133.1")),
            (date(2024, 1, 1), Decimal("146.41")),
        ]
        for day, nav in observations:
            AMFIMasterNAV.objects.update_or_create(
                scheme=self.scheme, date=day, source="AMFI",
                defaults={"nav": nav},
            )
        result = calculate_scheme_masterdata_metrics(self.scheme, date(2024, 1, 1))
        values = result["values"]
        self.assertIsNotNone(values["rolling_3y_cagr_mean_pct"])
        self.assertGreater(values["rolling_3y_cagr_observations"], 0)

