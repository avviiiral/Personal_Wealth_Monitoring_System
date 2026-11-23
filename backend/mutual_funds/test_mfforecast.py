import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

import requests
from django.core.management import call_command
from django.test import SimpleTestCase, TestCase

from mutual_funds.models import AMFIMasterScheme
from mutual_funds.master_models import (
    MFExternalPortfolioSnapshot,
    MFMasterHolding,
    MFSourceRun,
)
from mutual_funds.services.masterdata import persist_portfolio_rows
from mutual_funds.services.mfforecast import (
    build_mfforecast_headers,
    fetch_mfforecast_payload,
    normalize_mfforecast_base_url,
    parse_mfforecast_holdings,
)
from mutual_funds.services.masterdata_analytics import _holdings_metrics


FIXTURES = Path(__file__).resolve().parent / "tests" / "fixtures" / "masterdata"


def read_payload():
    return json.loads((FIXTURES / "mfforecast_holdings_sample.json").read_text(encoding="utf-8"))


class MFForecastRapidAPIConfigTests(SimpleTestCase):
    def test_strips_full_scheme_endpoint_from_configured_api_base_url(self):
        self.assertEqual(
            normalize_mfforecast_base_url(
                "https://indian-mutual-fund-portfolio-holding.p.rapidapi.com/funds/120503/holdings"
            ),
            "https://indian-mutual-fund-portfolio-holding.p.rapidapi.com",
        )

    def test_rapidapi_headers_include_host_and_key_separately(self):
        headers = build_mfforecast_headers(
            base_url="https://indian-mutual-fund-portfolio-holding.p.rapidapi.com",
            api_key="secret-value",
            api_key_header="x-rapidapi-key",
        )
        self.assertEqual(
            headers,
            {
                "X-RapidAPI-Key": "secret-value",
                "X-RapidAPI-Host": "indian-mutual-fund-portfolio-holding.p.rapidapi.com",
            },
        )

    def test_rapidapi_key_header_defaults_when_not_configured(self):
        headers = build_mfforecast_headers(
            base_url="https://indian-mutual-fund-portfolio-holding.p.rapidapi.com",
            api_key="secret-value",
        )
        self.assertEqual(headers["X-RapidAPI-Key"], "secret-value")
        self.assertEqual(
            headers["X-RapidAPI-Host"],
            "indian-mutual-fund-portfolio-holding.p.rapidapi.com",
        )

    def test_rejects_full_header_line_in_key_header_setting(self):
        with self.assertRaisesMessage(ValueError, "not a full header line"):
            build_mfforecast_headers(
                base_url="https://indian-mutual-fund-portfolio-holding.p.rapidapi.com",
                api_key="secret-value",
                api_key_header=(
                    "x-rapidapi-host: "
                    "indian-mutual-fund-portfolio-holding.p.rapidapi.com"
                ),
            )


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

    def test_empty_holdings_array_is_flagged_as_incomplete_provider_data(self):
        parsed = parse_mfforecast_holdings(
            {"scheme_name": "Debt Fund", "holdings": []},
            scheme_code="128629",
        )
        self.assertEqual(parsed["holdings_count"], 0)
        self.assertIn("MFFORECAST_HOLDINGS_EMPTY", parsed["quality_flags"])

    def test_skips_bad_holding_rows_and_flags_weight_coverage(self):
        payload = read_payload()
        payload["holdings"].append({"stock_name": "", "weight_pct": "not-a-number"})
        parsed = parse_mfforecast_holdings(payload, scheme_code="120503")
        self.assertEqual(parsed["holdings_count"], 1)
        self.assertEqual(parsed["invalid_rows"], 1)
        self.assertIn("MFFORECAST_INVALID_HOLDING_ROWS_SKIPPED", parsed["quality_flags"])


class MFForecastFetchTests(SimpleTestCase):
    @patch("mutual_funds.services.mfforecast._throttle")
    def test_fetch_uses_documented_path_and_disables_redirects(self, throttle):
        response = requests.Response()
        response.status_code = 200
        response._content = b'{"holdings": []}'
        session = Mock()
        session.get.return_value = response
        payload = {"holdings": []}

        with TemporaryDirectory() as temp_dir:
            with patch(
                "mutual_funds.services.mfforecast.source_raw_directory",
                return_value=Path(temp_dir),
            ), patch.object(response, "json", return_value=payload):
                result, endpoint, raw_path = fetch_mfforecast_payload(
                    session=session,
                    base_url="https://api.example.test/v1",
                    scheme_code="120503",
                )

        self.assertEqual(result, payload)
        self.assertEqual(endpoint, "https://api.example.test/v1/funds/120503/holdings")
        self.assertTrue(Path(raw_path).is_file())
        self.assertEqual(session.get.call_args.kwargs["allow_redirects"], False)
        self.assertIn("Accept", session.get.call_args.kwargs["headers"])
        throttle.assert_called_once()

    @patch("mutual_funds.services.mfforecast._throttle")
    def test_fetch_returns_none_on_404(self, throttle):
        response = requests.Response()
        response.status_code = 404
        session = Mock()
        session.get.return_value = response

        payload, endpoint, raw_path = fetch_mfforecast_payload(
            session=session, base_url="https://api.example.test", scheme_code="120503",
        )

        self.assertIsNone(payload)
        self.assertEqual(endpoint, "https://api.example.test/funds/120503/holdings")
        self.assertEqual(raw_path, "")

    @patch("mutual_funds.services.mfforecast._throttle")
    def test_fetch_rejects_redirect_before_forwarding_custom_headers(self, throttle):
        response = requests.Response()
        response.status_code = 302
        response.headers["Location"] = "https://other.example.test/collect"
        session = Mock()
        session.get.return_value = response

        with self.assertRaisesMessage(ValueError, "Redirects are disabled"):
            fetch_mfforecast_payload(
                session=session,
                base_url="https://api.example.test",
                scheme_code="120503",
                headers={"x-api-key": "test-secret"},
            )

        session.get.assert_called_once()
        throttle.assert_called_once()

class MFForecastEmptyResponseCommandTests(TestCase):
    def setUp(self):
        self.scheme = AMFIMasterScheme.objects.create(
            scheme_code="128629",
            scheme_name="HDFC Banking and PSU Debt Fund - Direct Plan - Growth",
            amc_name="HDFC Mutual Fund",
            isin_growth="INF179KA1IZ7",
            plan="Direct Plan",
            option="Growth",
            is_growth_plan=True,
            is_active=True,
        )

    @patch("mutual_funds.management.commands.sync_mfforecast_holdings.fetch_mfforecast_payload")
    def test_empty_holdings_response_is_audited_as_partial_not_success(self, fetch):
        fetch.return_value = (
            {
                "scheme_name": "HDFC Banking and PSU Debt Fund",
                "fund_house": "HDFC Mutual Fund",
                "category": "Banking and PSU",
                "aum_cr": 5000,
                "expense_ratio": 0.4,
                "asset_allocation": {"equity_pct": 0, "debt_pct": 95, "cash_pct": 5},
                "holdings": [],
            },
            "https://api.example.test/funds/128629/holdings",
            "",
        )

        with patch.dict(
            "os.environ",
            {"MFFORECAST_API_KEY": "", "MFFORECAST_API_KEY_HEADER": ""},
        ):
            call_command(
                "sync_mfforecast_holdings",
                scheme_codes=["128629"],
                base_url="https://api.example.test",
                limit=1,
            )

        run = MFSourceRun.objects.get(source="MFFORECAST_API")
        snapshot = MFExternalPortfolioSnapshot.objects.get(scheme=self.scheme)
        self.assertEqual(run.status, MFSourceRun.Status.PARTIAL)
        self.assertEqual(run.metadata["schemes_empty_holdings"], 1)
        self.assertEqual(snapshot.holdings_count, 0)
        self.assertIn("MFFORECAST_HOLDINGS_EMPTY", snapshot.quality_flags)
        self.assertFalse(MFMasterHolding.objects.filter(scheme=self.scheme).exists())
        self.assertIn("empty holdings array", run.errors[0]["error"])


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
