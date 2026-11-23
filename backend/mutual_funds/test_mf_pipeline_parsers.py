"""Parser tests for the masterdata ingestion pipeline.

Every fixture under tests/fixtures/mf_pipeline is a trimmed copy of a real
AMFI / mfapi.in response captured on 23-Nov-2026. No test makes HTTP calls.
"""
import io
import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pandas as pd
from django.test import SimpleTestCase

from mutual_funds.services.mf_pipeline import parsers

FIXTURES = Path(__file__).resolve().parent / "tests" / "fixtures" / "mf_pipeline"


def fixture_text(name):
    return (FIXTURES / name).read_text(encoding="utf-8")


def fixture_json(name):
    return json.loads(fixture_text(name))


def ter_export_xlsx_bytes():
    """Re-create AMFI's TER Excel export (header + rows + footnotes) in memory."""
    frame = pd.read_csv(FIXTURES / "amfi_ter_export_2026-10.csv", dtype=object)
    buffer = io.BytesIO()
    frame.to_excel(buffer, index=False)
    return buffer.getvalue()


class ParentNameNormalisationTests(SimpleTestCase):
    def test_case_punctuation_and_ampersand_are_normalised(self):
        self.assertEqual(
            parsers.normalize_parent_name("ADITYA BIRLA SUN LIFE MIDCAP FUND"),
            parsers.normalize_parent_name("Aditya Birla Sun Life Midcap Fund"),
        )
        self.assertEqual(
            parsers.normalize_parent_name("Large & Mid Cap Fund"),
            parsers.normalize_parent_name("large and mid-cap  fund"),
        )

    def test_mid_cap_spelling_variants_stay_distinct(self):
        # Exact matching only: "Mid Cap" and "Midcap" are different strings and
        # must not be silently merged by the normaliser.
        self.assertNotEqual(
            parsers.normalize_parent_name("HDFC Mid Cap Fund"),
            parsers.normalize_parent_name("HDFC Midcap Fund"),
        )


class SchemeDataCsvTests(SimpleTestCase):
    def test_parses_codes_parent_names_and_concatenated_isins(self):
        rows = parsers.parse_scheme_data_csv(fixture_text("amfi_scheme_data_midcap.csv"))
        by_code = {row["amfi_code"]: row for row in rows}

        self.assertEqual(by_code["118989"]["parent_name"], "HDFC Mid Cap Fund")
        self.assertEqual(by_code["118989"]["isins"], ["INF179K01XQ0"])
        # AMFI concatenates payout and reinvestment ISINs without a separator.
        self.assertEqual(by_code["101591"]["isins"], ["INF209K01355", "INF209K01CN6"])
        self.assertEqual(by_code["101592"]["launch_date"], date(2002, 9, 9))
        self.assertEqual(by_code["101592"]["category"], "Equity Scheme - Mid Cap Fund")


class TerExportTests(SimpleTestCase):
    def test_keeps_latest_date_per_scheme_and_uses_total_ter(self):
        rows = parsers.parse_ter_export(ter_export_xlsx_bytes(), "ter.xlsx")
        by_code = {row["nsdl_code"]: row for row in rows}

        self.assertEqual(len(rows), 3)  # footnote rows are ignored
        hdfc = by_code["HDFC/O/E/MIF/07/04/0015"]
        self.assertEqual(hdfc["parent_name"], "HDFC Mid Cap Fund")
        self.assertEqual(hdfc["ter_date"], date(2026, 10, 31))
        self.assertEqual(hdfc["regular_ter_pct"], Decimal("1.3100"))
        self.assertEqual(hdfc["direct_ter_pct"], Decimal("0.7400"))

    def test_rejects_file_without_ter_columns(self):
        frame = pd.DataFrame({"Scheme Name": ["X"], "Something": [1]})
        buffer = io.BytesIO()
        frame.to_excel(buffer, index=False)
        with self.assertRaises(ValueError):
            parsers.parse_ter_export(buffer.getvalue(), "bad.xlsx")


class RiskParameterTests(SimpleTestCase):
    def test_parses_nested_volatility_block(self):
        rows = parsers.parse_risk_parameters(
            fixture_json("amfi_risk_parameters_cat17_2026-10.json"), category_id=17,
        )
        by_id = {row["risk_scheme_id"]: row for row in rows}

        hdfc = by_id["2526"]
        self.assertEqual(hdfc["scheme_name"], "HDFC Mid Cap Fund")
        self.assertEqual(hdfc["report_month"], date(2026, 10, 1))
        self.assertEqual(hdfc["portfolio_std_dev_pct"], Decimal("16.0800"))
        self.assertEqual(hdfc["benchmark_std_dev_pct"], Decimal("18.5800"))
        self.assertEqual(hdfc["portfolio_beta"], Decimal("0.8221"))
        self.assertEqual(hdfc["category_id"], "17")
        # AMFI's disclosure has no Sharpe ratio field at all.
        self.assertNotIn("sharpe", {key.casefold() for key in hdfc})

    def test_empty_payload_returns_no_rows(self):
        self.assertEqual(parsers.parse_risk_parameters([], category_id=17), [])
        self.assertEqual(parsers.parse_risk_parameters({"data": []}, category_id=18), [])


class AaumTests(SimpleTestCase):
    def test_periods_resolve_to_quarter_bounds(self):
        periods = parsers.parse_aaum_periods(fixture_json("amfi_aaum_periods_fy2026-27.json"))
        self.assertEqual(periods[0]["label"], "July - September 2026")
        self.assertEqual(periods[0]["quarter_start"], date(2026, 7, 1))
        self.assertEqual(periods[0]["quarter_end"], date(2026, 9, 30))
        self.assertEqual(periods[1]["quarter_start"], date(2026, 4, 1))

    def test_scheme_rows_keyed_by_amfi_code_in_lakh(self):
        rows = parsers.parse_aaum_schemewise(fixture_json("amfi_aaum_schemewise_apr-jun-2026.json"))
        by_code = {row["amfi_code"]: row for row in rows}

        self.assertEqual(by_code["118989"]["amount_lakh"], Decimal("2684358.6600"))
        # Domestic FoF schemes use the FoF column, not the (zero) main column.
        self.assertEqual(by_code["132175"]["amount_lakh"], Decimal("449.2000"))

    def test_quarter_bounds_rejects_unknown_label(self):
        with self.assertRaises(ValueError):
            parsers.quarter_bounds("Sometime 2026")


class TrackingErrorTests(SimpleTestCase):
    def test_parses_regular_and_direct(self):
        rows = parsers.parse_tracking_error(fixture_json("amfi_tracking_error_2026-10-31.json"))
        self.assertEqual(rows[0]["parent_name"], "SBI BSE 100 ETF")
        self.assertEqual(rows[0]["as_of"], date(2026, 10, 31))
        self.assertEqual(rows[2]["regular_pct"], Decimal("0.1200"))
        self.assertEqual(rows[2]["benchmark"], "BSE PSU BANK (TR)")


class MfapiTests(SimpleTestCase):
    def test_parses_meta_and_navs(self):
        parsed = parsers.parse_mfapi_payload(fixture_json("mfapi_118989_2026-11.json"))
        self.assertEqual(parsed["scheme_code"], "118989")
        self.assertEqual(parsed["isin_growth"], "INF179K01XQ0")
        self.assertEqual(parsed["navs"][0], (date(2026, 11, 13), Decimal("229.697")))
        self.assertEqual(parsed["navs"][-1], (date(2026, 11, 20), Decimal("232.517")))

    def test_bad_rows_are_skipped(self):
        parsed = parsers.parse_mfapi_payload({
            "meta": {"scheme_code": 1},
            "data": [{"date": "bad", "nav": "1"}, {"date": "01-01-2026", "nav": "0"},
                     {"date": "02-01-2026", "nav": "N.A."}, {"date": "03-01-2026", "nav": "10.5"}],
        })
        self.assertEqual(parsed["navs"], [(date(2026, 1, 3), Decimal("10.5"))])


class RiskFreeTableTests(SimpleTestCase):
    def test_parses_date_and_rate_columns(self):
        payload = b"Date,91 Day T-Bill Yield (%)\n2026-11-19,6.8500\n20-Nov-2026,6.87%\n,\n"
        rows = parsers.parse_risk_free_table(payload, "tbill.csv")
        self.assertEqual(rows, [
            {"date": date(2026, 11, 19), "rate_pct": Decimal("6.8500")},
            {"date": date(2026, 11, 20), "rate_pct": Decimal("6.8700")},
        ])
