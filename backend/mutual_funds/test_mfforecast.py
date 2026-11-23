import json
from datetime import date
from decimal import Decimal
from pathlib import Path

from django.test import TestCase

from mutual_funds.models import AMFIMasterScheme
from mutual_funds.master_models import (
    MFExternalPortfolioSnapshot,
    MFMasterHolding,
)
from mutual_funds.services.masterdata import persist_portfolio_rows
from mutual_funds.services.mfforecast import parse_mfforecast_holdings
from mutual_funds.services.masterdata_analytics import _holdings_metrics


FIXTURES = Path(__file__).resolve().parent / "tests" / "fixtures" / "masterdata"


def read_payload():
    return json.loads((FIXTURES / "mfforecast_holdings_sample.json").read_text(encoding="utf-8"))


class MFForecastParserTests(TestCase):
    def test_parses_documented_endpoint_example(self):
        parsed = parse_mfforecast_holdings(read_payload(), scheme_code="120503")

        self.assertEqual(parsed["scheme_code"], "120503")
        self.assertEqual(parsed["scheme_name"], "Axis Bluechip Fund - Growth")
        self.assertEqual(parsed["aum_cr"], Decimal("34521.4500"))
        self.assertEqual(parsed["expense_ratio_pct"], Decimal("1.6400"))
        self.assertEqual(parsed["asset_allocation"]["equity_pct"], "97.2000")
        self.assertEqual(parsed["holdings_count"], 1)
        self.assertEqual(parsed["holdings"][0]["security_name"], "HDFC Bank Ltd.")
        self.assertEqual(parsed["holdings"][0]["ticker"], "HDFCBANK")
        self.assertEqual(parsed["holdings"][0]["isin"], "INE040A01034")
        self.assertEqual(parsed["holdings"][0]["quantity"], Decimal("4250000.0000"))
        self.assertEqual(parsed["holdings"][0]["pct_of_nav"], Decimal("9.8200"))
        self.assertEqual(parsed["holdings"][0]["instrument_type"], "EQUITY")

    def test_rejects_missing_holdings_array(self):
        with self.assertRaisesMessage(ValueError, "holdings array"):
            parse_mfforecast_holdings({"scheme_name": "Fund"}, scheme_code="120503")

    def test_skips_bad_holding_rows_and_flags_weight_coverage(self):
        payload = read_payload()
        payload["holdings"].append({"stock_name": "", "weight_pct": "not-a-number"})
        parsed = parse_mfforecast_holdings(payload, scheme_code="120503")
        self.assertEqual(parsed["holdings_count"], 1)
        self.assertEqual(parsed["invalid_rows"], 1)
        self.assertIn("MFFORECAST_INVALID_HOLDING_ROWS_SKIPPED", parsed["quality_flags"])


class MFForecastPersistenceAndAnalyticsTests(TestCase):
    def setUp(self):
        self.scheme = AMFIMasterScheme.objects.create(
            scheme_code="120503",
            scheme_name="Axis Bluechip Fund - Direct Plan - Growth",
            isin_growth="INF000A01001",
            amc_name="Axis Mutual Fund",
            plan="Direct Plan",
            option="Growth",
            is_growth_plan=True,
            is_active=True,
        )

    def test_provider_metadata_and_fetch_date_holdings_are_persisted_separately(self):
        parsed = parse_mfforecast_holdings(read_payload(), scheme_code=self.scheme.scheme_code)
        observed_date = date(2026, 11, 23)
        snapshot = MFExternalPortfolioSnapshot.objects.create(
            scheme=self.scheme,
            provider="MFFORECAST",
            observed_at="2026-11-23T10:00:00Z",
            scheme_name=parsed["scheme_name"],
            fund_house=parsed["fund_house"],
            category=parsed["category"],
            aum_cr=parsed["aum_cr"],
            expense_ratio_pct=parsed["expense_ratio_pct"],
            asset_allocation=parsed["asset_allocation"],
            holdings_count=parsed["holdings_count"],
            total_weight_pct=parsed["total_weight_pct"],
            source_url="https://mfforecast.com/funds/120503/holdings",
        )
        result = persist_portfolio_rows(
            self.scheme,
            parsed["holdings"],
            portfolio_date=observed_date,
            portfolio_date_basis=MFMasterHolding.DATE_BASIS_FETCHED_AT,
            source="MFFORECAST_API",
            source_url="https://mfforecast.com/funds/120503/holdings",
        )

        self.assertEqual(result["inserted"], 1)
        holding = MFMasterHolding.objects.get(scheme=self.scheme)
        self.assertEqual(holding.portfolio_date_basis, MFMasterHolding.DATE_BASIS_FETCHED_AT)
        self.assertEqual(holding.ticker, "HDFCBANK")
        self.assertEqual(snapshot.aum_cr, Decimal("34521.4500"))
        self.assertEqual(snapshot.expense_ratio_pct, Decimal("1.6400"))

        values, flags, source_url = _holdings_metrics(self.scheme, observed_date)
        self.assertEqual(values["equity_pct"], Decimal("97.2000"))
        self.assertEqual(values["debt_pct"], Decimal("0.0000"))
        self.assertEqual(values["cash_pct"], Decimal("2.8000"))
        self.assertIn("PORTFOLIO_DATE_IS_FETCH_DATE", flags)
        self.assertIn("MFFORECAST_ASSET_ALLOCATION_USED", flags)
        self.assertEqual(source_url, "https://mfforecast.com/funds/120503/holdings")

    def test_disclosed_amc_portfolio_takes_precedence_over_newer_fetch_date_snapshot(self):
        parsed = parse_mfforecast_holdings(read_payload(), scheme_code=self.scheme.scheme_code)
        observed_date = date(2026, 11, 23)
        MFExternalPortfolioSnapshot.objects.create(
            scheme=self.scheme,
            provider="MFFORECAST",
            observed_at="2026-11-23T10:00:00Z",
            asset_allocation=parsed["asset_allocation"],
            source_url="https://mfforecast.com/funds/120503/holdings",
        )
        persist_portfolio_rows(
            self.scheme,
            parsed["holdings"],
            portfolio_date=observed_date,
            portfolio_date_basis=MFMasterHolding.DATE_BASIS_FETCHED_AT,
            source="MFFORECAST_API",
            source_url="https://mfforecast.com/funds/120503/holdings",
        )
        persist_portfolio_rows(
            self.scheme,
            [{
                "security_name": "Official AMC Equity",
                "pct_of_nav": Decimal("100.0000"),
                "instrument_type": "EQUITY",
            }],
            portfolio_date=date(2026, 10, 31),
            portfolio_date_basis=MFMasterHolding.DATE_BASIS_DISCLOSURE,
            source="AMC_PORTFOLIO_HDFC",
            source_url="https://amc.example/portfolio",
        )

        values, flags, source_url = _holdings_metrics(self.scheme, observed_date)
        self.assertEqual(values["equity_pct"], Decimal("100.0000"))
        self.assertNotIn("PORTFOLIO_DATE_IS_FETCH_DATE", flags)
        self.assertEqual(source_url, "https://amc.example/portfolio")
