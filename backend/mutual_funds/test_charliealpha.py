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
    MFProviderBenchmark,
    MFProviderBenchmarkObservation,
    MFSchemeIdentityMap,
    MFSourceRun,
)
from mutual_funds.services.charliealpha import (
    BASE_URL,
    NAV_SOURCE,
    SOURCE,
    CharlieAlphaClient,
    CharlieAlphaService,
    _records,
    _category_records,
    _benchmark_window_params,
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
    def test_grouped_categories_preserve_asset_class_and_canonical_slug(self):
        rows = _category_records({
            "categories": {
                "Equity": [
                    {"slug": "equity-large-cap", "name": "Large Cap"},
                    {"slug": "equity-flexi-cap", "name": "Flexi Cap"},
                ],
                "ETF": ["index-etf"],
            }
        })

        self.assertEqual(
            [(row["slug"], row["asset_class"]) for row in rows],
            [
                ("equity-large-cap", "Equity"),
                ("equity-flexi-cap", "Equity"),
                ("index-etf", "ETF"),
            ],
        )

    def test_benchmark_window_validation_accepts_exact_dates_and_rejects_partial_range(self):
        self.assertEqual(
            _benchmark_window_params(from_date="2025-01-01", to_date="2025-12-31"),
            {"from": "2025-01-01", "to": "2025-12-31"},
        )
        self.assertEqual(_benchmark_window_params("5y"), {"range": "5Y"})
        with self.assertRaises(ValueError):
            _benchmark_window_params(from_date="2025-01-01")
        with self.assertRaises(ValueError):
            _benchmark_window_params(from_date="2025-12-31", to_date="2025-01-01")
        with self.assertRaises(ValueError):
            _benchmark_window_params("2y")

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

        # Disable only the inter-request throttle; this test targets 429 retry handling.
        with patch("mutual_funds.services.charliealpha.MIN_REQUEST_INTERVAL", 0.0), patch(
            "mutual_funds.services.charliealpha._LAST_REQUEST_AT", 0.0
        ):
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
        # MFSourceRun.metadata is JSON-safe; Decimal base values are strings.
        self.assertEqual(run.metadata["normalization"]["base_value"], "100")
        self.assertEqual(run.metadata["observation_count"], 2)
        self.assertIn("not an absolute official", run.metadata["normalization"]["warning"])
        provider_benchmark = MFProviderBenchmark.objects.get(provider="CHARLIEALPHA", slug="nifty-500")
        observations = MFProviderBenchmarkObservation.objects.filter(benchmark=provider_benchmark).order_by("observation_date")
        self.assertEqual(observations.count(), 2)
        self.assertEqual(observations.first().normalized_value, Decimal("100"))
        self.assertEqual(observations.first().normalization_base_date, date(2025, 10, 1))


    def test_nav_history_persists_daily_rows_without_daily_master_snapshots(self):
        client = Mock()
        client.get.return_value = {
            "dates": ["2026-10-27", "2026-10-28"],
            "nav": [Decimal("124.5"), Decimal("125.0")],
        }
        result = CharlieAlphaService(client=client).sync_nav_history([self.scheme.scheme_code])

        self.assertEqual(result["fetched"], 2)
        self.assertEqual(
            AMFIMasterNAV.objects.filter(scheme=self.scheme, source=NAV_SOURCE).count(),
            2,
        )
        self.assertFalse(MFMasterData.objects.filter(scheme=self.scheme).exists())

    def test_historical_holdings_snapshots_preserve_each_provider_report_date(self):
        client = Mock()
        client.get.side_effect = [
            {
                "periods": [
                    {"as_of": "2026-09-30", "holdings_count": 1},
                    {"as_of": "2026-08-31", "holdings_count": 1},
                ],
                "scheme_name": self.scheme.scheme_name,
                "latest_as_of": "2026-09-30",
            },
            {
                "as_of": "2026-09-30",
                "holdings": [{
                    "name": "Test Bank Ltd.",
                    "isin": "INE000T01010",
                    "weight_pct": "5.25",
                    "sector": "Banks",
                }],
            },
            {
                "as_of": "2026-08-31",
                "holdings": [{
                    "name": "Test Bank Ltd.",
                    "isin": "INE000T01010",
                    "weight_pct": "4.75",
                    "sector": "Banks",
                }],
            },
        ]
        service = CharlieAlphaService(client=client)
        result = service.sync_holdings_history([self.scheme.scheme_code], period_limit=0)

        dates = set(
            MFMasterHolding.objects.filter(scheme=self.scheme, source=SOURCE)
            .values_list("portfolio_date", flat=True)
        )
        self.assertEqual(result["historical_snapshots_requested"], 2)
        self.assertEqual(dates, {date(2026, 9, 30), date(2026, 8, 31)})
        self.assertTrue(
            MFSourceRun.objects.filter(
                source=SOURCE,
                command="holdings_periods",
                metadata__scope="scheme",
            ).exists()
        )


    def test_ter_history_respects_direct_plan_label(self):
        client = Mock()
        client.get.return_value = {
            "dates": ["2026-10-31"],
            "ter": ["0.75"],
        }
        result = CharlieAlphaService(client=client).sync_ter([self.scheme.scheme_code])

        self.assertEqual(result["fetched"], 1)
        snapshot = MFMasterData.objects.get(scheme=self.scheme, as_of_date=date(2026, 10, 31))
        self.assertEqual(snapshot.ter_direct_pct, Decimal("0.75"))
        self.assertIsNone(snapshot.ter_regular_pct)
        self.assertEqual(snapshot.ter_source, SOURCE)

    def test_provider_identifier_conflict_is_rejected_even_in_dry_run(self):
        other_scheme = AMFIMasterScheme.objects.create(
            scheme_code="990502",
            scheme_name="PWMS Other CharlieAlpha Test Fund",
            isin_growth="INF990A01502",
            amc_name="PWMS Test AMC",
        )
        MFSchemeIdentityMap.objects.create(
            source="CHARLIEALPHA",
            external_id="provider-internal-42",
            scheme=self.scheme,
            external_name=self.scheme.scheme_name,
            match_method="ISIN_EXACT",
            status=MFSchemeIdentityMap.STATUS_ACTIVE,
        )
        service = CharlieAlphaService(client=Mock(), dry_run=True)

        resolved = service._resolve_scheme({
            "scheme_code": "provider-internal-42",
            "isin": other_scheme.isin_growth,
            "scheme_name": other_scheme.scheme_name,
        })

        self.assertIsNone(resolved)
        self.assertEqual(service.stats["unmatched"], 1)
        self.assertEqual(
            service.errors[0]["kind"],
            "provider_identifier_scheme_conflict",
        )

    def test_supported_valuation_duration_and_credit_metrics_map_without_correlation_to_r_squared(self):
        client = Mock()
        client.get.return_value = {
            "scheme_code": self.scheme.scheme_code,
            "inception_date": "2018-04-12",
            "metrics": {
                "3y": {
                    "trailing_cagr": "14.5",
                    "treynor_ratio": "0.92",
                    "risk_free_rate": "6.75",
                    "ratio_window_years": "3",
                    "pe": "25.4",
                    "pb": "3.2",
                    "dividend_yield": "1.15",
                    "turnover_ratio": "0.38",
                    "large_cap_pct": "72.5",
                    "mid_cap_pct": "18.5",
                    "small_cap_pct": "9.0",
                    "ytm": "7.1",
                    "modified_duration": "4.25",
                    "macaulay_duration": "4.5",
                    "average_maturity": "5.2",
                    "credit_rating_split": {"AAA": "62.5", "AA": "37.5"},
                    "correlation": "0.87",
                }
            },
        }

        CharlieAlphaService(client=client).sync_metrics([self.scheme.scheme_code])

        snapshot = MFMasterData.objects.get(scheme=self.scheme)
        self.assertEqual(snapshot.treynor, Decimal("0.92"))
        self.assertEqual(snapshot.risk_free_rate_used_pct, Decimal("6.75"))
        self.assertEqual(snapshot.ratio_window_years, Decimal("3"))
        self.assertEqual(snapshot.pe, Decimal("25.4"))
        self.assertEqual(snapshot.pb, Decimal("3.2"))
        self.assertEqual(snapshot.dividend_yield_pct, Decimal("1.15"))
        self.assertEqual(snapshot.turnover_ratio, Decimal("0.38"))
        self.assertEqual(snapshot.large_cap_pct, Decimal("72.5"))
        self.assertEqual(snapshot.modified_duration, Decimal("4.25"))
        self.assertEqual(snapshot.macaulay_duration, Decimal("4.5"))
        self.assertEqual(snapshot.average_maturity, Decimal("5.2"))
        self.assertEqual(snapshot.credit_rating_split["AAA"], "62.5")
        self.assertEqual(snapshot.inception_date, date(2018, 4, 12))
        self.assertIsNone(snapshot.r_squared)

        run = MFSourceRun.objects.get(
            source=SOURCE,
            command="metrics",
            metadata__scheme_code=self.scheme.scheme_code,
        )
        self.assertEqual(run.metadata["provider_response"]["metrics"]["3y"]["correlation"], "0.87")

    def test_scheme_metrics_request_isin_is_used_when_provider_response_has_internal_code(self):
        client = Mock()
        client.get.return_value = {
            "scheme_code": "provider-internal-77",
            "metrics": {
                "3y": {
                    "trailing_cagr": "15.75",
                    "volatility": "11.2",
                }
            },
        }

        CharlieAlphaService(client=client).sync_metrics(
            scheme_codes=[self.scheme.isin_growth],
        )

        client.get.assert_called_once_with(
            "mf/metrics/" + self.scheme.isin_growth,
        )
        snapshot = MFMasterData.objects.get(scheme=self.scheme)
        self.assertEqual(snapshot.return_3y_pct, Decimal("15.75"))
        self.assertTrue(
            MFSchemeIdentityMap.objects.filter(
                source="CHARLIEALPHA",
                external_id="provider-internal-77",
                scheme=self.scheme,
                status=MFSchemeIdentityMap.STATUS_ACTIVE,
            ).exists()
        )

    def test_category_scheme_discovery_returns_exact_amfi_linked_codes(self):
        client = Mock()
        client.get.side_effect = [
            {"categories": {"Equity": [{"slug": "equity-large-cap", "name": "Large Cap"}]}},
            {"schemes": [{
                "scheme_code": self.scheme.scheme_code,
                "isin": self.scheme.isin_growth,
                "scheme_name": self.scheme.scheme_name,
                "amc": "Provider AMC Name",
                "category": "Large Cap",
                "plan": "Regular Plan",
                "option": "IDCW",
            }]},
        ]

        result = CharlieAlphaService(client=client).sync_schemes(category="equity-large-cap")
        run = MFSourceRun.objects.get(source=SOURCE, command="schemes")

        self.assertEqual(result["linked_scheme_codes"], [self.scheme.scheme_code])
        self.assertEqual(run.metadata["category_slug"], "equity-large-cap")
        self.assertEqual(run.metadata["linked_scheme_codes"], [self.scheme.scheme_code])
        # Existing AMFI identity metadata is not overwritten by provider labels.
        self.scheme.refresh_from_db()
        self.assertEqual(self.scheme.amc_name, "PWMS Test AMC")
        self.assertEqual(self.scheme.plan, "Direct Plan")
        self.assertEqual(self.scheme.option, "Growth")

    def test_category_catalog_is_persisted_with_asset_class_slugs(self):
        client = Mock()
        client.get.return_value = {
            "categories": {
                "Equity": [{"slug": "equity-large-cap", "name": "Large Cap"}],
                "ETF": ["index-etf"],
            }
        }

        result = CharlieAlphaService(client=client).sync_categories()
        run = MFSourceRun.objects.get(source=SOURCE, command="categories")

        self.assertEqual(result["categories"], 2)
        self.assertEqual(
            run.metadata["category_slugs"],
            ["equity-large-cap", "index-etf"],
        )
        self.assertEqual(
            {row["asset_class"] for row in run.metadata["category_rows"]},
            {"Equity", "ETF"},
        )

    def test_category_metrics_pass_horizon_filter_and_persist_medians(self):
        client = Mock()
        client.get.return_value = {
            "schemes": [{
                "scheme_code": self.scheme.scheme_code,
                "scheme_name": self.scheme.scheme_name,
                "metrics": {
                    "3y": {
                        "trailing_cagr": "14.25",
                        "volatility": "12.5",
                        "sharpe": "1.1",
                    }
                },
            }],
            "category_medians": {
                "3y": {"trailing_cagr": {"median": "11.2", "sample_count": 20}}
            },
        }

        CharlieAlphaService(client=client).sync_metrics(
            category_slugs=["equity-large-cap"],
            horizon="3y",
        )

        client.get.assert_called_once_with(
            "mf/metrics-by-cat/equity-large-cap",
            {"horizon": "3y"},
        )
        category_run = MFSourceRun.objects.get(
            source=SOURCE,
            command="category_metrics",
            metadata__category_slug="equity-large-cap",
        )
        self.assertEqual(category_run.metadata["horizon"], "3y")
        self.assertEqual(category_run.metadata["metrics_count"], 1)
        self.assertEqual(
            category_run.metadata["category_medians"]["3y"]["trailing_cagr"]["sample_count"],
            20,
        )
        snapshot = MFMasterData.objects.get(scheme=self.scheme)
        self.assertEqual(snapshot.return_3y_pct, Decimal("14.25"))
        self.assertEqual(snapshot.std_dev_pct, Decimal("12.5"))

    def test_nav_history_accepts_scheme_isin_as_provider_identifier(self):
        client = Mock()
        client.get.return_value = {
            "dates": ["2026-10-27"],
            "nav": ["125.5"],
        }

        result = CharlieAlphaService(client=client).sync_nav_history([self.scheme.isin_growth])

        client.get.assert_called_once_with("mf/hnav/" + self.scheme.isin_growth)
        self.assertEqual(result["fetched"], 1)
        self.assertTrue(
            AMFIMasterNAV.objects.filter(
                scheme=self.scheme,
                source=NAV_SOURCE,
                date=date(2026, 10, 27),
                nav=Decimal("125.500000"),
            ).exists()
        )

    def test_benchmark_history_requests_custom_window_and_keeps_normalization_provenance(self):
        client = Mock()
        client.get.side_effect = [
            {"benchmarks": [{"slug": "nifty-500", "name": "Nifty 500", "category": "Equity"}]},
            {"dates": ["2025-01-01", "2025-12-31"], "nav": ["100", "115.2"]},
        ]

        CharlieAlphaService(client=client).sync_benchmarks(
            from_date="2025-01-01",
            to_date="2025-12-31",
        )

        self.assertEqual(
            client.get.call_args_list[1].args,
            ("benchmark/hnav/nifty-500", {"from": "2025-01-01", "to": "2025-12-31"}),
        )
        run = MFSourceRun.objects.get(source=SOURCE, command="benchmark_history")
        self.assertEqual(
            run.metadata["requested_window"],
            {"from": "2025-01-01", "to": "2025-12-31"},
        )
        self.assertEqual(
            run.metadata["normalization"]["base_date"],
            "2025-01-01",
        )

    def test_compare_preserves_provider_default_and_only_requests_pairwise_when_enabled(self):
        client = Mock()
        client.get.return_value = {"summary": {"funds": 2}}

        service = CharlieAlphaService(client=client)
        service.compare(["990501", "990502"])
        client.get.assert_called_once_with(
            "mf/compare",
            {"codes": "990501,990502"},
        )

        client.get.reset_mock()
        service.compare(["990501", "990502"], include_pairwise=True)
        client.get.assert_called_once_with(
            "mf/compare",
            {"codes": "990501,990502", "include_pairwise": "true"},
        )

    def test_empty_holdings_are_recorded_as_partial_not_zero_portfolio(self):
        client = Mock()
        client.get.return_value = {
            "as_of": "2026-09-30",
            "total_holdings": 0,
            "holdings": [],
        }

        result = CharlieAlphaService(client=client).sync_holdings([self.scheme.scheme_code])
        run = MFSourceRun.objects.get(
            source=SOURCE,
            command="holdings_unavailable",
            metadata__scheme_code=self.scheme.scheme_code,
        )

        self.assertEqual(result["failed"], 1)
        self.assertEqual(run.status, MFSourceRun.Status.PARTIAL)
        self.assertEqual(run.metadata["data_status"], "NOT_RETURNED_OR_NO_VALID_HOLDINGS")
        self.assertFalse(MFMasterHolding.objects.filter(scheme=self.scheme, source=SOURCE).exists())

    def test_fixed_income_holding_fields_and_asset_allocation_are_persisted(self):
        client = Mock()
        client.get.return_value = {
            "as_of": "2026-09-30",
            "holdings": [{
                "name": "Government of India 7.18% 2033",
                "isin": "IN0020230010",
                "weight_pct": "6.5",
                "asset_type": "DEBT",
                "rating": "SOVEREIGN",
                "rating_agency": "CRISIL",
                "maturity_date": "2033-08-14",
                "ytm_pct": "7.18",
            }],
        }

        CharlieAlphaService(client=client).sync_holdings([self.scheme.scheme_code])

        holding = MFMasterHolding.objects.get(
            scheme=self.scheme,
            source=SOURCE,
            portfolio_date=date(2026, 9, 30),
        )
        snapshot = MFMasterData.objects.get(scheme=self.scheme, as_of_date=date(2026, 9, 30))
        external = MFExternalPortfolioSnapshot.objects.get(
            scheme=self.scheme,
            provider="CHARLIEALPHA",
            provider_as_of_date=date(2026, 9, 30),
        )
        self.assertEqual(holding.rating, "SOVEREIGN")
        self.assertEqual(holding.rating_agency, "CRISIL")
        self.assertEqual(holding.maturity_date, date(2033, 8, 14))
        self.assertEqual(holding.ytm_pct, Decimal("7.18"))
        self.assertEqual(snapshot.debt_pct, Decimal("6.5"))
        self.assertEqual(external.asset_allocation["debt"], "6.5")

