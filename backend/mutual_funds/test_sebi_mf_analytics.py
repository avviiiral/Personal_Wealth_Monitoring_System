from datetime import date, timedelta

from django.test import SimpleTestCase

from mutual_funds.services.sebi_mf_analytics import (
    CATEGORY_TREE,
    _add_category_benchmarks,
    calculate_pairwise_portfolio_overlap,
    compute_fund_performance_metrics,
    map_sebi_hierarchy,
    parse_amfi_navall_feed,
)

import pandas as pd


class SEBITaxonomyTests(SimpleTestCase):
    def test_current_taxonomy_has_forty_leaf_categories(self):
        current = [
            category
            for category, (branch, _group) in CATEGORY_TREE.items()
            if category != "Unclassified" and branch != "Solution Oriented (Discontinued)"
        ]
        self.assertEqual(len(current), 40)

    def test_solution_oriented_is_retained_as_discontinued_legacy(self):
        mapped = map_sebi_hierarchy(
            "Open Ended Schemes (Solution Oriented Scheme - Retirement Fund)",
            "Example Retirement Fund - Direct Plan - Growth",
        )
        self.assertEqual(mapped["level_1_branch"], "Solution Oriented (Discontinued)")
        self.assertEqual(mapped["classification_status"], "DISCONTINUED")

    def test_amfi_six_column_feed_preserves_amc_and_raw_header(self):
        source = (
            "Scheme Code;ISIN Div Payout/ ISIN Growth;ISIN Div Reinvestment;Scheme Name;Net Asset Value;Date\n"
            "Open Ended Schemes (Equity Scheme - Large Cap Fund)\n"
            "Example Mutual Fund\n"
            "100001;INF000000001;;Example Large Cap Fund - Direct Plan - Growth;25.5000;19-Nov-2026\n"
        )
        records = parse_amfi_navall_feed(source)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["amc_name"], "Example Mutual Fund")
        self.assertEqual(records[0]["raw_category_header"], "Open Ended Schemes (Equity Scheme - Large Cap Fund)")
        self.assertEqual(records[0]["level_1_branch"], "Equity")
        self.assertEqual(records[0]["level_3_category"], "Large Cap")
        self.assertTrue(records[0]["is_direct_plan_growth"])

    def test_fund_of_funds_header_preserves_nested_parentheses(self):
        mapped = map_sebi_hierarchy(
            "Open Ended Schemes (Other Schemes - FoFs (Overseas/Domestic))",
            "Example Overseas Fund of Funds - Direct Plan - Growth",
        )
        self.assertEqual(mapped["level_1_branch"], "Other Schemes")
        self.assertEqual(mapped["level_3_category"], "Fund of Funds (Overseas/Domestic)")

    def test_debt_sectoral_fund_is_not_mapped_to_equity_sectoral(self):
        mapped = map_sebi_hierarchy(
            "Open Ended Schemes (Debt Scheme - Sectoral Fund)",
            "Example Financial Services Debt Fund - Direct Plan - Growth",
        )
        self.assertEqual(mapped["level_1_branch"], "Debt")
        self.assertEqual(mapped["level_3_category"], "Sectoral Debt")

    def test_sectoral_and_thematic_are_separate_current_leaf_categories(self):
        sectoral = map_sebi_hierarchy(
            "Open Ended Schemes (Equity Scheme - Sectoral/Thematic Fund)",
            "Example Sectoral Fund - Direct Plan - Growth",
        )
        thematic = map_sebi_hierarchy(
            "Open Ended Schemes (Equity Scheme - Sectoral/Thematic Fund)",
            "Example Thematic Fund - Direct Plan - Growth",
        )
        self.assertEqual(sectoral["level_3_category"], "Sectoral")
        self.assertEqual(thematic["level_3_category"], "Thematic")

    def test_legacy_navall_isin_columns_are_assigned_to_growth_or_idcw(self):
        source = (
            "Scheme Code;ISIN Div Payout/ ISIN Growth;ISIN Div Reinvestment;Scheme Name;Net Asset Value;Date\n"
            "Open Ended Schemes (Equity Scheme - Large Cap Fund)\n"
            "Example AMC\n"
            "100010;INF-GROWTH;;Example Large Cap Fund - Direct Plan - Growth;25.5;19-Nov-2026\n"
            "100011;INF-PAYOUT;INF-REINVEST;Example Large Cap Fund - Direct Plan - IDCW Reinvestment;24.5;19-Nov-2026\n"
        )
        records = {row["scheme_code"]: row for row in parse_amfi_navall_feed(source)}
        self.assertEqual(records["100010"]["isin_growth"], "INF-GROWTH")
        self.assertEqual(records["100010"]["isin_dividend"], "")
        self.assertEqual(records["100011"]["isin_growth"], "")
        self.assertEqual(records["100011"]["isin_dividend"], "INF-REINVEST")

    def test_regular_growth_is_separate_from_direct_growth(self):
        source = (
            "Scheme Code;ISIN Growth;ISIN Dividend;Scheme Name;Plan;Option;Net Asset Value;Date\n"
            "Open Ended Schemes (Equity Scheme - Flexi Cap Fund)\n"
            "Example AMC\n"
            "100002;INF000000002;;Example Flexi Cap Fund;Direct Plan;Growth;25.5;19-Nov-2026\n"
            "100003;INF000000003;;Example Flexi Cap Fund;Regular Plan;Growth;24.5;19-Nov-2026\n"
        )
        records = parse_amfi_navall_feed(source)
        self.assertEqual(len(records), 2)
        self.assertEqual(sum(row["is_direct_plan_growth"] for row in records), 1)
        self.assertEqual(sum(row["is_growth_plan"] for row in records), 2)


class SEBIPerformanceTests(SimpleTestCase):
    def test_daily_nav_metrics_include_cagr_volatility_and_drawdown(self):
        start = date(2023, 1, 1)
        values = []
        for index in range(1100):
            # Smooth positive path, then a discrete drawdown, then recovery.
            nav = 100.0 + index * 0.05
            if 700 <= index <= 750:
                nav -= 15.0
            if index > 750:
                nav += min(15.0, (index - 750) * 0.1)
            values.append((start + timedelta(days=index), nav))
        metrics = compute_fund_performance_metrics(values)
        self.assertIn("CAGR_1Y_pct", metrics)
        self.assertIn("CAGR_3Y_pct", metrics)
        self.assertIn("CAGR_5Y_pct", metrics)
        self.assertIn("CAGR_10Y_pct", metrics)
        self.assertIn("Rolling_3Y_CAGR_Mean_pct", metrics)
        self.assertIn("Annualized_Volatility_pct", metrics)
        self.assertIn("Sharpe_Ratio", metrics)
        self.assertIn("Sortino_Ratio", metrics)
        self.assertLess(metrics["Max_Drawdown_pct"], 0)
        self.assertGreater(metrics["Max_Drawdown_Peak_to_Trough_Days"], 0)

    def test_four_fund_peer_group_assigns_one_fund_to_each_quartile(self):
        base = {
            "Level_1_Branch": "Equity",
            "Level_2_Group": "By Market Cap",
            "Level_3_Category": "Large Cap",
            "Plan": "Direct Plan",
            "CAGR_1Y_pct": 10.0,
            "CAGR_5Y_pct": 10.0,
            "Rolling_3Y_CAGR_Mean_pct": 10.0,
            "Annualized_Volatility_pct": 12.0,
            "Sharpe_Ratio": 0.8,
            "Max_Drawdown_pct": -20.0,
            "Sortino_Ratio": 1.0,
        }
        funds = pd.DataFrame([
            {**base, "Scheme_Code": "1", "Scheme_Name": "Fund A", "CAGR_3Y_pct": 30.0},
            {**base, "Scheme_Code": "2", "Scheme_Name": "Fund B", "CAGR_3Y_pct": 20.0},
            {**base, "Scheme_Code": "3", "Scheme_Name": "Fund C", "CAGR_3Y_pct": 10.0},
            {**base, "Scheme_Code": "4", "Scheme_Name": "Fund D", "CAGR_3Y_pct": 0.0},
        ])
        _summary, ranked = _add_category_benchmarks(funds, plans=["Direct Plan"])
        quartiles = dict(zip(ranked["Scheme_Code"], ranked["Quartile"]))
        self.assertEqual(quartiles, {"1": "Q1", "2": "Q2", "3": "Q3", "4": "Q4"})

    def test_category_peer_rankings_split_direct_and_regular(self):
        funds = pd.DataFrame([
            {"Scheme_Code": "1", "Scheme_Name": "Direct A", "Level_1_Branch": "Equity", "Level_2_Group": "By Market Cap", "Level_3_Category": "Large Cap", "Plan": "Direct Plan", "CAGR_1Y_pct": 12.0, "CAGR_3Y_pct": 15.0, "CAGR_5Y_pct": 14.0, "Rolling_3Y_CAGR_Mean_pct": 13.0, "Annualized_Volatility_pct": 12.0, "Sharpe_Ratio": 0.8, "Max_Drawdown_pct": -20.0, "Sortino_Ratio": 1.0},
            {"Scheme_Code": "2", "Scheme_Name": "Direct B", "Level_1_Branch": "Equity", "Level_2_Group": "By Market Cap", "Level_3_Category": "Large Cap", "Plan": "Direct Plan", "CAGR_1Y_pct": 8.0, "CAGR_3Y_pct": 10.0, "CAGR_5Y_pct": 9.0, "Rolling_3Y_CAGR_Mean_pct": 9.0, "Annualized_Volatility_pct": 13.0, "Sharpe_Ratio": 0.4, "Max_Drawdown_pct": -25.0, "Sortino_Ratio": 0.6},
            {"Scheme_Code": "3", "Scheme_Name": "Regular C", "Level_1_Branch": "Equity", "Level_2_Group": "By Market Cap", "Level_3_Category": "Large Cap", "Plan": "Regular Plan", "CAGR_1Y_pct": 5.0, "CAGR_3Y_pct": 7.0, "CAGR_5Y_pct": 6.0, "Rolling_3Y_CAGR_Mean_pct": 6.0, "Annualized_Volatility_pct": 14.0, "Sharpe_Ratio": 0.2, "Max_Drawdown_pct": -30.0, "Sortino_Ratio": 0.3},
        ])
        summary, ranked = _add_category_benchmarks(funds, plans=["Direct Plan", "Regular Plan"])
        direct = ranked[ranked["Plan"] == "Direct Plan"]
        regular = ranked[ranked["Plan"] == "Regular Plan"]
        self.assertEqual(float(direct["Category_Avg_CAGR_3Y_pct"].iloc[0]), 12.5)
        self.assertEqual(float(regular["Category_Avg_CAGR_3Y_pct"].iloc[0]), 7.0)
        self.assertEqual(len(summary), 40 * 2)


class SEBIPortfolioOverlapTests(SimpleTestCase):
    def test_overlap_sums_minimum_weights_for_common_isins_only(self):
        portfolios = [
            {
                "scheme_code": "1",
                "scheme_name": "Fund A",
                "level_3_category": "Large Cap",
                "holdings": [
                    {"isin": "INE000000001", "weight_pct": 10.0},
                    {"isin": "INE000000002", "weight_pct": 20.0},
                    {"isin": "INE000000003", "weight_pct": 5.0},
                ],
            },
            {
                "scheme_code": "2",
                "scheme_name": "Fund B",
                "level_3_category": "Large Cap",
                "holdings": [
                    {"isin": "INE000000001", "weight_pct": 15.0},
                    {"isin": "INE000000002", "weight_pct": 8.0},
                    {"isin": "INE000000004", "weight_pct": 9.0},
                ],
            },
            {
                "scheme_code": "3",
                "scheme_name": "Other Category",
                "level_3_category": "Mid Cap",
                "holdings": [{"isin": "INE000000001", "weight_pct": 99.0}],
            },
        ]
        rows = calculate_pairwise_portfolio_overlap(portfolios)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["Common_ISIN_Count"], 2)
        self.assertEqual(rows[0]["Portfolio_Overlap_pct"], 18.0)
