from datetime import date
from decimal import Decimal
from unittest.mock import Mock, patch

from django.test import SimpleTestCase, TestCase

from mutual_funds.models import AMFIMasterNAV, AMFIMasterScheme
from mutual_funds.master_models import (
    MFBenchmarkTRI,
    MFExternalPortfolioSnapshot,
    MFMasterData,
    MFMasterHolding,
    MFSourceRun,
)
from mutual_funds.services.charliealpha import (
    BASE_URL,
    NAV_SOURCE,
    SOURCE,
    CharlieAlphaClient,
    CharlieAlphaService,
    _records,
    decimal_value,
    parse_date,
)


class CharlieAlphaParserTests(SimpleTestCase):
    def test_compact_fields_and_two_dimensional_data_are_decoded(self):
        rows = _records({
            "fields": ["scheme_code", "scheme_name", "date", "nav"],
            "data": [
                ["120503", "PWMS Large Cap Fund - Direct Plan - Growth", "2026-10-28", "125.4567"],
                ["120504", "PWMS Mid Cap Fund - Direct Plan - Growth", "2026-10-28", "98.1234"],
            ],
        })
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["scheme_code"], "120503")
        self.assertEqual(rows[1]["nav"], "98.1234")

    def test_decimal_and_date_parsers_preserve_missing_values(self):
        self.assertEqual(decimal_value("1,234.567%"), Decimal("1234.567"))
        self.assertIsNone(decimal_value("N/A"))
        self.assertIsNone(decimal_value(None))
        self.assertEqual(parse_date("28-10-2026"), date(2026, 10, 28))
        self.assertEqual(parse_date("2026-10-28"), date(2026, 10, 28))
        self.assertIsNone(parse_date("not-a-date"))

    @patch("mutual_funds.services.charliealpha.time.sleep")
    def test_client_sends_json_accept_header_and_uses_finite_timeouts(self, _sleep):
        response = Mock()
        response.status_code = 200
        response.headers = {}
        response.json.return_value = {"categories": []}
        session = Mock()
        session.headers = {}
        session.get.return_value = response

        client = CharlieAlphaClient(session=session)
        payload = client.get("mf/categories")

        self.assertEqual(payload, {"categories": []})
        self.assertEqual(session.headers["Accept"], "application/json")
        args, kwargs = session.get.call_args
        self.assertEqual(args[0], BASE_URL + "/mf/categories")
        self.assertEqual(kwargs["timeout"], (5, 30))

    @patch("mutual_funds.services.charliealpha.time.sleep")
    def test_client_retries_rate_limited_response(self, sleep):
        limited = Mock()
        limited.status_code = 429
        limited.headers = {"Retry-After": "0"}
        ok = Mock()
        ok.status_code = 200
        ok.headers = {}
        ok.json.return_value = {"ok": True}
        session = Mock()
        session.headers = {}
        session.get.side_effect = [limited, ok]

        payload = CharlieAlphaClient(session=session).get("mf/categories")

        self.assertEqual(payload, {"ok": True})
        self.assertEqual(session.get.call_count, 2)
        sleep.assert_called_once_with(0.0)


class CharlieAlphaPersistenceTests(TestCase):
    def setUp(self):
        self.scheme = AMFIMasterScheme.objects.create(
            scheme_code="990501",
            scheme_name="PWMS CharlieAlpha Test Fund - Direct Plan - Growth",
            isin_growth="INF990A01501",
            amc_name="PWMS Test AMC",
            plan="Direct Plan",
            option="Growth",
            is_growth_plan=True,
            sebi_universe_active=True,
            level_1_branch="Equity",
            level_2_group="By Market Cap",
            level_3_category="Large Cap",
        )

    def test_latest_nav_is_database_persisted_and_repeat_sync_is_idempotent(self):
        client = Mock()
        client.get.return_value = {
            "fields": ["scheme_code", "isin", "scheme_name", "date", "nav"],
            "data": [[
                self.scheme.scheme_code,
                self.scheme.isin_growth,
                self.scheme.scheme_name,
                "2026-10-28",
                "125.4567",
            ]],
        }
        service = CharlieAlphaService(client=client)

        first = service.sync_navall()
        second = service.sync_navall()

        self.assertEqual(first["fetched"], 1)
        self.assertEqual(second["fetched"], 2)
        self.assertEqual(
            AMFIMasterNAV.objects.filter(scheme=self.scheme, source=NAV_SOURCE).count(),
            1,
        )
        nav = AMFIMasterNAV.objects.get(scheme=self.scheme, source=NAV_SOURCE)
        self.assertEqual(nav.nav, Decimal("125.456700"))
        snapshot = MFMasterData.objects.get(scheme=self.scheme, as_of_date=date(2026, 10, 28))
        self.assertEqual(snapshot.nav, Decimal("125.4567"))
        self.assertEqual(snapshot.nav_source, SOURCE)
        self.assertEqual(snapshot.field_sources["nav"]["source"], SOURCE)
        self.assertEqual(
            MFSourceRun.objects.filter(source=SOURCE, command="navall").count(),
            2,
        )

    def test_holdings_use_provider_specific_keys_and_store_snapshot_in_database(self):
        client = Mock()
        client.get.return_value = {
            "as_of": "2026-09-30",
            "holdings": [{
                "security_name": "Test Bank Ltd.",
                "isin": "INE000T01010",
                "ticker": "TESTBANK",
                "weight_pct": "5.25",
                "quantity": "100",
                "market_value": "125000",
                "asset_type": "EQUITY",
                "sector": "Banks",
                "industry": "Private Banks",
            }],
            "changes": {"added": [], "exited": []},
        }
        service = CharlieAlphaService(client=client)

        service.sync_holdings([self.scheme.scheme_code])
        service.sync_holdings([self.scheme.scheme_code])

        holdings = MFMasterHolding.objects.filter(
            scheme=self.scheme,
            source=SOURCE,
            portfolio_date=date(2026, 9, 30),
        )
        self.assertEqual(holdings.count(), 1)
        holding = holdings.get()
        self.assertEqual(holding.security_key, "CHARLIEALPHA:INE000T01010")
        self.assertEqual(holding.pct_of_nav, Decimal("5.2500"))
        self.assertEqual(holding.sector, "Banks")
        snapshot = MFMasterData.objects.get(scheme=self.scheme, as_of_date=date(2026, 9, 30))
        self.assertEqual(snapshot.top10_holdings[0]["name"], "Test Bank Ltd.")
        self.assertEqual(snapshot.sector_allocation["Banks"], "5.25")
        provider_snapshot = MFExternalPortfolioSnapshot.objects.get(
            scheme=self.scheme,
            provider="CHARLIEALPHA",
            provider_as_of_date=date(2026, 9, 30),
        )
        self.assertEqual(provider_snapshot.holdings_count, 1)
        self.assertTrue(
            MFSourceRun.objects.filter(
                source=SOURCE,
                command="holdings_changes",
                metadata__scheme_code=self.scheme.scheme_code,
            ).exists()
        )

    def test_rebased_benchmark_history_is_not_written_to_official_tri_table(self):
        client = Mock()
        client.get.side_effect = [
            {"benchmarks": [{"slug": "nifty-500", "name": "Nifty 500 TRI", "category": "Equity"}]},
            {"dates": ["2025-10-01", "2025-10-02"], "nav": [100, 101.25]},
        ]
        result = CharlieAlphaService(client=client).sync_benchmarks()

        self.assertEqual(result["benchmarks"], 1)
        self.assertFalse(MFBenchmarkTRI.objects.exists())
        run = MFSourceRun.objects.get(source=SOURCE, command="benchmark_history")
        self.assertTrue(run.metadata["normalization"]["is_normalized"])
        self.assertEqual(run.metadata["normalization"]["base_value"], 100.0)
        self.assertEqual(run.metadata["series"][0]["normalized_value"], "100")
        self.assertIn("not an absolute official", run.metadata["normalization"]["warning"])
