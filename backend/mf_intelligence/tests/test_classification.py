from datetime import date

from django.test import SimpleTestCase

from mf_intelligence.services.classification import (
    CASH_LIKE,
    CORPORATE,
    SOVEREIGN,
    fixed_income_issuer_type,
    fund_match_key,
    maturity_bucket,
    maturity_from_name,
    sector_style,
    short_fund_name,
)
from mf_intelligence.services.dashboard import _sleeve_is_material


class MaturityParsingTests(SimpleTestCase):
    def test_formats_seen_in_real_disclosures(self):
        cases = {
            "7.30% Bharti Telecom Ltd. (01/12/2027)": date(2027, 12, 1),
            "91 Days Tbill (MD 17/12/2026)": date(2026, 12, 17),
            "DSP FINANCE PRIVATE LTD. CP RED 09-11-26": date(2026, 11, 9),
            "NABARD CP 01-Jan-2027": date(2027, 1, 1),
            "CD - BANK OF INDIA - 08/02/2027": date(2027, 2, 8),
            "Kotak Mahindra Prime Limited 24-NOV-2026": date(2026, 11, 24),
            "364 DAY T-BILL 19.11.26": date(2026, 11, 19),
            "06.48% GSEC Mat- 06/10/2035": date(2035, 10, 6),
            "Union Bank of India (15-Dec-2026)": date(2026, 12, 15),
        }
        for name, expected in cases.items():
            with self.subTest(name=name):
                self.assertEqual(maturity_from_name(name), expected)

    def test_names_without_a_full_date(self):
        for name in ("7.1% Power Finance Corporation Limited", "Union Bank of India 2026", "", None, "Cash", "(31/02/2027)"):
            with self.subTest(name=name):
                self.assertIsNone(maturity_from_name(name))

    def test_maturity_bucket_boundaries(self):
        self.assertEqual(maturity_bucket(0), "< 1Y")
        self.assertEqual(maturity_bucket(0.999), "< 1Y")
        self.assertEqual(maturity_bucket(1), "1-3Y")
        self.assertEqual(maturity_bucket(9.99), "5-10Y")
        self.assertEqual(maturity_bucket(10), "10Y+")


class LabelTests(SimpleTestCase):
    def test_issuer_types(self):
        self.assertEqual(fixed_income_issuer_type("IN0020260025", "Government of India (11/05/2036)"), SOVEREIGN)
        self.assertEqual(fixed_income_issuer_type("IN1920160067", "7.27% State Government Securities"), SOVEREIGN)
        self.assertEqual(fixed_income_issuer_type("", "91 Days Tbill"), SOVEREIGN)
        self.assertEqual(fixed_income_issuer_type("", "Call, Cash & Other Assets"), CASH_LIKE)
        self.assertEqual(fixed_income_issuer_type("", "TREPS"), CASH_LIKE)
        self.assertEqual(fixed_income_issuer_type("INE403D08306", "7.30% Bharti Telecom Ltd. (01/12/2027)"), CORPORATE)
        self.assertEqual(fixed_income_issuer_type(None, None), CORPORATE)

    def test_sector_style(self):
        self.assertEqual(sector_style("Banks"), "Cyclical")
        self.assertEqual(sector_style(" IT - Software "), "Defensive")
        self.assertEqual(sector_style("Power"), "Sensitive")
        self.assertEqual(sector_style("Others"), "Other")
        self.assertEqual(sector_style(""), "Other")
        self.assertEqual(sector_style(None), "Other")

    def test_regular_and_direct_names_share_a_match_key(self):
        pairs = (
            ("Nippon India Multi Cap Fund-Growth Plan-Growth Option", "Nippon India Multi Cap Fund - Direct Plan Growth Plan - Growth Option"),
            ("JM Large Cap Fund (Regular) - Growth Option", "JM Large Cap Fund (Direct) - Growth Option"),
            ("Nippon India Vision Large & Midcap Fund-GROWTH PLAN-Growth Option", "Nippon India Vision Large & Mid Cap Fund - Direct Plan Growth Plan - Growth Option"),
            ("Groww Liquid Fund (formerly known as Indiabulls Liquid Fund) - Regular Plan - Growth Option", "Groww Liquid Fund ( formerly known as Indiabulls Liquid )Fund - Direct Plan - Growth Option"),
        )
        for regular, direct in pairs:
            with self.subTest(regular=regular):
                self.assertEqual(fund_match_key(regular), fund_match_key(direct))
        self.assertNotEqual(fund_match_key("Alpha Small Cap Fund"), fund_match_key("Alpha Mid Cap Fund"))
        self.assertEqual(fund_match_key(""), "")

    def test_short_name_drops_plan_and_option(self):
        self.assertEqual(short_fund_name("Axis Multicap Fund - Regular Plan - Growth"), "Axis Multicap Fund")
        self.assertEqual(short_fund_name("LIC MF Overnight Fund-Direct Plan-Growth"), "LIC MF Overnight Fund")
        self.assertEqual(short_fund_name("Groww Gold ETF"), "Groww Gold ETF")
        self.assertEqual(short_fund_name(""), "")


class MaterialSleeveTests(SimpleTestCase):
    def fund(self, equity_pct):
        return {"holdings": {"stocks": 5 if equity_pct else 0, "equity_pct": equity_pct}}

    def has_equity(self, holdings):
        return holdings.get("stocks") and holdings.get("equity_pct", 0) >= 10

    def test_needs_a_quarter_of_funds_with_holdings(self):
        funds = [self.fund(95)] + [self.fund(0) for _ in range(3)] + [{"holdings": None}]
        self.assertTrue(_sleeve_is_material(funds, self.has_equity))  # 1 of 4 with holdings
        funds = [self.fund(95)] + [self.fund(0) for _ in range(4)]
        self.assertFalse(_sleeve_is_material(funds, self.has_equity))  # 1 of 5

    def test_weight_threshold_boundary(self):
        self.assertTrue(_sleeve_is_material([self.fund(10)], self.has_equity))
        self.assertFalse(_sleeve_is_material([self.fund(9.99)], self.has_equity))

    def test_no_holdings_is_never_material(self):
        self.assertFalse(_sleeve_is_material([], self.has_equity))
        self.assertFalse(_sleeve_is_material([{"holdings": None}], self.has_equity))
