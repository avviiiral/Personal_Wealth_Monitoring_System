from datetime import date, timedelta

from django.test import SimpleTestCase

from mutual_funds.models import AMFIMasterScheme

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


    def test_mojibake_children_fund_header_maps_to_discontinued_legacy_category(self):
        mapped = map_sebi_hierarchy(
            "Open Ended Schemes(Solution Oriented Scheme - Children\u00e2\u0080\u0099s Fund)",
        )
        self.assertEqual(mapped["level_1_branch"], "Solution Oriented (Discontinued)")
        self.assertEqual(mapped["level_3_category"], "Children's Fund")
        self.assertEqual(mapped["classification_status"], "DISCONTINUED")

    def test_income_debt_wrappers_map_to_current_debt_leaf_categories(self):
        cases = {
            "Open Ended Schemes(Income/Debt Oriented Schemes - Ultra Short to Short Term Fund)": "Ultra Short to Short Term",
            "Open Ended Schemes(Income/Debt Oriented Schemes - Ultra Short Term Fund)": "Ultra Short Term",
            "Open Ended Schemes(Income/Debt Oriented Schemes - Short Term Fund)": "Short Term",
            "Open Ended Schemes(Income/Debt Oriented Schemes - Dynamic Term Fund)": "Dynamic Term",
            "Open Ended Schemes(Income/Debt Oriented Schemes - Banking and PSU Debt Fund)": "Banking and PSU Debt",
            "Open Ended Schemes(Income/Debt Oriented Schemes - Medium to Long Term Fund)": "Medium to Long Term",
            "Open Ended Schemes(Income/Debt Oriented Schemes - Money Market Fund)": "Money Market",
            "Open Ended Schemes(Income/Debt Oriented Schemes - Medium Term Fund)": "Medium Term",
            "Open Ended Schemes(Income/Debt Oriented Schemes - Gilt Fund)": "Gilt",
            "Open Ended Schemes(Income/Debt Oriented Schemes - Floating Interest Rates Fund)": "Floating Interest Rates",
            "Open Ended Schemes(Income/Debt Oriented Schemes - Long Term Fund)": "Long Term",
            "Open Ended Schemes(Income/Debt Oriented Schemes - 10-year Constant Maturity Gilt Fund)": "10-year Constant Maturity Gilt",
        }
        for header, expected in cases.items():
            with self.subTest(header=header):
                mapped = map_sebi_hierarchy(header)
                self.assertEqual(mapped["level_3_category"], expected)
                self.assertEqual(mapped["classification_status"], "MAPPED")

    def test_other_scheme_and_nested_fund_of_funds_headers_map(self):
        headers = [
            "Open Ended Schemes(Other Scheme - FoF Domestic)",
            "Open Ended Schemes(Fund of Funds Scheme (Domestic) - Fund of Funds Scheme (Domestic))",
            "Open Ended Schemes(Other Scheme - FoF Overseas)",
            "Open Ended Schemes(Overseas Fund of Funds - Fund of Funds investing overseas)",
        ]
        for header in headers:
            with self.subTest(header=header):
                mapped = map_sebi_hierarchy(header)
                self.assertEqual(mapped["level_1_branch"], "Other Schemes")
                self.assertEqual(mapped["level_3_category"], "Fund of Funds (Overseas/Domestic)")

    def test_equity_savings_and_passive_etf_headers_map(self):
        cases = {
            "Open Ended Schemes(Hybrid Scheme - Equity Savings)": "Equity Savings",
            "Open Ended Schemes(Other Scheme - Index Funds)": "Index Funds/ETFs",
            "Open Ended Schemes(Exchange Traded Funds (ETFs) - Equity ETF)": "Index Funds/ETFs",
            "Open Ended Schemes(Exchange Traded Funds (ETFs) - Gold ETF)": "Index Funds/ETFs",
            "Open Ended Schemes(Exchange Traded Funds (ETFs) - Silver ETF)": "Index Funds/ETFs",
            "Open Ended Schemes(Exchange Traded Funds (ETFs) - Debt ETF)": "Index Funds/ETFs",
        }
        for header, expected in cases.items():
            with self.subTest(header=header):
                self.assertEqual(map_sebi_hierarchy(header)["level_3_category"], expected)

    def test_sectoral_thematic_combined_header_is_not_guessed_from_scheme_name(self):
        mapped = map_sebi_hierarchy(
            "Open Ended Schemes(Equity Scheme - Sectoral/ Thematic)",
            "Example Thematic Opportunities Fund - Direct Plan - Growth",
        )
        self.assertEqual(mapped["level_3_category"], "Unclassified")
        self.assertEqual(mapped["classification_status"], "UNMAPPED")

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
            "Open Ended Schemes (Equity Scheme - Sectoral Fund)",
        )
        thematic = map_sebi_hierarchy(
            "Open Ended Schemes (Equity Scheme - Thematic Fund)",
        )
        self.assertEqual(sectoral["level_3_category"], "Sectoral")
        self.assertEqual(thematic["level_3_category"], "Thematic")

    def test_combined_sectoral_thematic_header_is_not_inferred_from_scheme_name(self):
        for scheme_name in (
            "Example Sectoral Fund - Direct Plan - Growth",
            "Example Thematic Fund - Direct Plan - Growth",
        ):
            with self.subTest(scheme_name=scheme_name):
                mapped = map_sebi_hierarchy(
                    "Open Ended Schemes (Equity Scheme - Sectoral/Thematic Fund)",
                    scheme_name,
                )
                self.assertEqual(mapped["level_3_category"], "Unclassified")

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


    def test_eight_column_feed_preserves_long_amfi_option_labels(self):
        long_option = "Monthly Payout of Income Distribution cum capital withdrawal option"
        source = (
            "Scheme Code;ISIN Div Payout/ ISIN Growth;ISIN Div Reinvestment;Scheme Name;Plan;Option;Net Asset Value;Date\n"
            "Open Ended Schemes (Debt Scheme - Banking and PSU Fund)\n"
            "Example AMC\n"
            f"100020;INF000000020;;Example Banking and PSU Fund;Direct Plan;{long_option};12.5;20-Nov-2026\n"
            "100021;INF000000021;;Example Banking and PSU Fund;Direct Plan;Growth Option;25.5;20-Nov-2026\n"
        )
        records = {row["scheme_code"]: row for row in parse_amfi_navall_feed(source)}

        self.assertEqual(records["100020"]["option"], long_option)
        self.assertFalse(records["100020"]["is_growth_plan"])
        self.assertEqual(records["100021"]["option"], "Growth")
        self.assertTrue(records["100021"]["is_direct_plan_growth"])
        self.assertGreater(len(long_option), 60)
        self.assertGreaterEqual(
            AMFIMasterScheme._meta.get_field("option").max_length,
            len(long_option),
        )


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
