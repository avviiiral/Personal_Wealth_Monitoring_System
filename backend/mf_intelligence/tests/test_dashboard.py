from django.test import SimpleTestCase, TestCase

from mf_intelligence.models import MFCategory, MFHolding, MFHoldingSnapshot, MFScheme, MFStockClassification
from mf_intelligence.services.dashboard import (
    UNCLASSIFIED,
    build_category_dashboard,
    build_scheme_detail,
    cap_bucket,
    categories_payload,
    percentile_ranks,
)
from mf_intelligence.services.sync import STEP_BENCHMARKS, STEP_HOLDINGS, STEP_NAV, MFIntelligenceSync
from mf_intelligence.tests.fakes import FUND_A, FUND_A_DIRECT, FUND_B, LIQUID_FUND, FakeCharlieAlpha


EXPECTED_PEERS = {FUND_A, FUND_B}  # IDCW, Direct and unclaimed plans excluded


class PercentileRankTests(SimpleTestCase):
    def test_mid_rank_percentiles(self):
        ranks = percentile_ranks({"a": 1, "b": 2, "c": 3, "d": None})
        self.assertEqual(ranks, {"a": 16.6667, "b": 50.0, "c": 83.3333})

    def test_lower_is_better_inverts(self):
        ranks = percentile_ranks({"a": 1, "b": 3}, higher_is_better=False)
        self.assertGreater(ranks["a"], ranks["b"])

    def test_ties_share_a_rank_and_single_value_is_median(self):
        self.assertEqual(percentile_ranks({"a": 5, "b": 5}), {"a": 50.0, "b": 50.0})
        self.assertEqual(percentile_ranks({"a": 5}), {"a": 50.0})
        self.assertEqual(percentile_ranks({}), {})
        self.assertEqual(percentile_ranks({"a": None}), {})

    def test_cap_bucket_rules(self):
        lookup = {"INE1": ("Large Cap", "AMFI")}
        self.assertEqual(cap_bucket("INE1", lookup, "AMFI"), "Large Cap")
        self.assertEqual(cap_bucket("INE2", lookup, "AMFI"), UNCLASSIFIED)
        # The index proxy only knows Large/Mid membership: other Indian equities are small.
        self.assertEqual(cap_bucket("INE2", lookup, "INDEX_PROXY"), "Small Cap")
        self.assertEqual(cap_bucket("US0378331005", lookup, "INDEX_PROXY"), UNCLASSIFIED)
        self.assertEqual(cap_bucket("", {}, None), UNCLASSIFIED)


class DashboardTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        api = FakeCharlieAlpha()
        sync = MFIntelligenceSync(client=api, workers=1)
        sync.run(categories=["multi-cap", "liquid"], steps=[STEP_NAV, STEP_HOLDINGS, STEP_BENCHMARKS])
        MFStockClassification.objects.create(isin="INE040A01034", cap_bucket="Large Cap", source="AMFI")
        MFStockClassification.objects.create(isin="INE009A01021", cap_bucket="Mid Cap", source="AMFI")
        cls.category = MFCategory.objects.get(slug="multi-cap")

    def dashboard(self, plan="Regular", horizon="3Y", benchmark=None):
        return build_category_dashboard(self.category, plan, horizon, benchmark)

    def fund(self, payload, code):
        return next(fund for fund in payload["funds"] if fund["code"] == code)

    def test_peer_group_is_growth_option_of_the_plan(self):
        payload = self.dashboard()
        self.assertEqual({fund["code"] for fund in payload["funds"]}, EXPECTED_PEERS)
        self.assertEqual(payload["plan"], "Regular")
        self.assertTrue(payload["flags"]["has_equity"])
        self.assertTrue(payload["flags"]["has_returns"])

    def test_category_average_is_simple_mean_of_peers(self):
        payload = self.dashboard()
        a, b = self.fund(payload, FUND_A), self.fund(payload, FUND_B)
        expected = round((a["returns"]["1y"] + b["returns"]["1y"]) / 2, 4)
        self.assertAlmostEqual(payload["category_average"]["returns"]["1y"], expected, places=3)
        self.assertEqual(payload["category_average"]["ter"], round((a["ter"] + b["ter"]) / 2, 4))

    def test_quant_score_rewards_stronger_fund(self):
        payload = self.dashboard()
        a, b = self.fund(payload, FUND_A), self.fund(payload, FUND_B)
        self.assertGreater(a["score"]["total"], b["score"]["total"])
        self.assertEqual(a["score"]["rank"], 1)
        self.assertEqual(set(a["score"]["components"]), {"returns", "risk_adjusted", "consistency", "downside", "cost"})

    def test_calmar_is_cagr_over_absolute_drawdown(self):
        fund = self.fund(self.dashboard(), FUND_A)
        metrics = fund["metrics"]["3Y"]
        self.assertEqual(fund["calmar"]["3Y"], round(metrics["trailing_cagr"] / abs(metrics["trailing_max_drawdown"]), 4))

    def test_only_selected_horizon_carries_every_metric(self):
        fund = self.fund(self.dashboard(horizon="5Y"), FUND_A)
        self.assertIn("rolling_xirr_p90", fund["metrics"]["5Y"])
        self.assertNotIn("rolling_xirr_p90", fund["metrics"]["1Y"])
        self.assertIn("sharpe_ratio", fund["metrics"]["1Y"])

    def test_holdings_summary_sectors_caps_and_unique_ideas(self):
        holdings = self.fund(self.dashboard(), FUND_A)["holdings"]
        self.assertEqual(holdings["stocks"], 3)
        self.assertEqual(holdings["sectors"], {"Banks": 9.0, "IT - Software": 6.0, "Power": 4.0})
        # Weights are rebased to the 19% equity sleeve: 9 / 6 / 4.
        self.assertAlmostEqual(holdings["cap"]["Large Cap"], 9 / 19 * 100, places=3)
        self.assertAlmostEqual(holdings["cap"]["Mid Cap"], 6 / 19 * 100, places=3)
        self.assertAlmostEqual(holdings["cap"][UNCLASSIFIED], 4 / 19 * 100, places=3)
        self.assertAlmostEqual(holdings["style"]["Cyclical"] + holdings["style"]["Defensive"] + holdings["style"]["Sensitive"], 100, places=3)
        self.assertEqual(self.dashboard()["sector_styles"], {"Banks": "Cyclical", "IT - Software": "Defensive", "Power": "Sensitive"})
        unique = self.fund(self.dashboard(), FUND_A)["unique"]
        self.assertEqual(unique["count"], 1)  # "Alpha Only Ltd."
        self.assertEqual(unique["weight_pct"], 4.0)

    def test_activity_aggregates_buys_and_exits_across_funds(self):
        activity = self.dashboard()["activity"]
        new_isins = {item["isin"] for item in activity["new_entries"]}
        exit_isins = {item["isin"] for item in activity["full_exits"]}
        self.assertIn("INE009A01021", new_isins)  # added by both funds
        self.assertIn("INE333C01033", exit_isins)  # exited by both funds
        hdfc = next(item for item in activity["net_buys"] if item["isin"] == "INE040A01034")
        self.assertEqual(hdfc["buyers"], 2)
        self.assertEqual(hdfc["total_change_pct"], 2.0)

    def test_direct_plan_borrows_regular_plan_holdings(self):
        payload = self.dashboard(plan="Direct")
        direct = self.fund(payload, FUND_A_DIRECT)
        self.assertEqual(direct["holdings_from_code"], FUND_A)
        self.assertEqual(direct["holdings"]["stocks"], 3)
        self.assertIsNone(direct["returns"]["1y"])  # Direct NAV was not synced

    def test_benchmark_defaults_to_category_index_and_can_be_overridden(self):
        self.assertEqual(self.dashboard()["benchmark"]["slug"], "nifty500-multicap-50-25-25-tri")
        self.assertEqual(self.dashboard(benchmark="nifty-500-tri")["benchmark"]["slug"], "nifty-500-tri")
        # Unknown slug falls back to the category default.
        self.assertEqual(self.dashboard(benchmark="missing")["benchmark"]["slug"], "nifty500-multicap-50-25-25-tri")

    def test_fixed_income_profile_for_debt_category(self):
        payload = build_category_dashboard(MFCategory.objects.get(slug="liquid"), "Regular", "1Y")
        fixed_income = payload["funds"][0]["holdings"]["fixed_income"]
        self.assertTrue(payload["flags"]["has_fixed_income"])
        self.assertFalse(payload["flags"]["has_equity"])
        self.assertEqual(fixed_income["sovereign_pct"], 40.0)
        self.assertEqual(fixed_income["corporate_pct"], 50.0)
        self.assertEqual(fixed_income["cash_equivalent_pct"], 0.0)
        self.assertEqual(fixed_income["money_market_pct"], 40.0)
        self.assertEqual(fixed_income["maturity_coverage_pct"], 100.0)
        self.assertIsNotNone(fixed_income["avg_maturity_years"])

    def test_empty_category_returns_empty_payload(self):
        empty = MFCategory.objects.create(slug="empty-cat", name="Empty", asset_class="Equity")
        payload = build_category_dashboard(empty, "Regular", "3Y")
        self.assertEqual(payload["funds"], [])
        self.assertFalse(payload["flags"]["has_holdings"])
        self.assertIsNone(payload["category_average"]["holdings"])
        self.assertIsNone(payload["category_average"]["returns"]["1y"])

    def test_scheme_detail_includes_growth_holdings_and_unique_stocks(self):
        detail = build_scheme_detail(MFScheme.objects.get(amfi_code=FUND_A))
        self.assertEqual(detail["growth"]["fund"][0], 100.0)
        self.assertEqual(len(detail["growth"]["fund"]), len(detail["growth"]["benchmark"]))
        self.assertEqual(len(detail["holdings"]["rows"]), 4)
        self.assertEqual(len(detail["holdings"]["exits"]), 1)
        self.assertEqual([row["name"] for row in detail["unique_stocks"]], ["Alpha Only Ltd."])
        self.assertEqual(detail["peers_count"], 1)
        hdfc = next(row for row in detail["holdings"]["rows"] if row["isin"] == "INE040A01034")
        self.assertEqual((hdfc["cap"], hdfc["style"]), ("Large Cap", "Cyclical"))

    def test_scheme_detail_without_any_holdings(self):
        MFHolding.objects.all().delete()
        MFHoldingSnapshot.objects.all().delete()
        detail = build_scheme_detail(MFScheme.objects.get(amfi_code=LIQUID_FUND))
        self.assertEqual(detail["holdings"]["rows"], [])
        self.assertIsNone(detail["holdings"]["summary"])
        self.assertEqual(detail["holding_history"], [])

    def test_categories_payload_groups_by_asset_class(self):
        payload = categories_payload()
        groups = {group["asset_class"]: group["categories"] for group in payload["groups"]}
        self.assertEqual([item["slug"] for item in groups["Equity"]], ["multi-cap"])
        multi = groups["Equity"][0]
        self.assertEqual(multi["funds"]["Regular"], 3)  # includes unclaimed plan in the raw count
        self.assertTrue(multi["has_data"])
        self.assertEqual(payload["last_sync"]["status"], "success")
