from datetime import date
from decimal import Decimal
from pathlib import Path

from django.test import TestCase

from mutual_funds.models import AMFIMasterScheme, MFMasterData, MFMasterHolding
from mutual_funds.services.masterdata import (
    calculate_benchmark_relative_metrics,
    parse_portfolio_rows,
    parse_ter_rows,
    persist_portfolio_rows,
    resolve_master_scheme,
)


FIXTURES = Path(__file__).resolve().parent / "tests" / "fixtures" / "masterdata"


def read_fixture(name):
    return (FIXTURES / name).read_bytes()


class MasterDataSchemeMatchingTests(TestCase):
    def setUp(self):
        self.scheme = AMFIMasterScheme.objects.create(
            scheme_code="120503",
            scheme_name="PWMS Large Cap Fund - Direct Plan - Growth",
            isin_growth="INF000A01001",
            amc_name="PWMS Asset Management Company",
            plan="Direct Plan",
            option="Growth",
            is_growth_plan=True,
            sebi_universe_active=True,
            level_3_category="Large Cap",
        )

    def test_isin_match_takes_precedence_over_variant_name(self):
        matched, status = resolve_master_scheme(
            isin="inf000a01001",
            scheme_name="Different spelling - Direct Growth",
        )
        self.assertEqual(matched.pk, self.scheme.pk)
        self.assertEqual(status, "isin")

    def test_unknown_isin_does_not_fall_back_to_name(self):
        matched, status = resolve_master_scheme(
            isin="INF999Z01099",
            scheme_name=self.scheme.scheme_name,
        )
        self.assertIsNone(matched)
        self.assertEqual(status, "unmatched")

    def test_exact_normalized_name_is_unique_fallback(self):
        matched, status = resolve_master_scheme(
            isin="",
            scheme_name="PWMS Large Cap Fund Direct Plan Growth",
        )
        self.assertEqual(matched.pk, self.scheme.pk)
        self.assertEqual(status, "name_exact")

    def test_ambiguous_exact_name_is_not_guessed(self):
        AMFIMasterScheme.objects.create(
            scheme_code="120504",
            scheme_name=self.scheme.scheme_name,
            isin_growth="INF000A01002",
            plan="Regular Plan",
            option="Growth",
            is_growth_plan=True,
            sebi_universe_active=True,
        )
        matched, status = resolve_master_scheme(isin="", scheme_name=self.scheme.scheme_name)
        self.assertIsNone(matched)
        self.assertEqual(status, "ambiguous")


class MasterDataParsingTests(TestCase):
    def test_ter_rows_parse_plan_and_percentage(self):
        rows = parse_ter_rows(
            read_fixture("amfi_ter_sample.tsv"),
            filename="amfi_ter_sample.tsv",
            as_of_date=date(2026, 10, 31),
        )
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["plan"], "Direct Plan")
        self.assertEqual(rows[0]["ter_pct"], Decimal("0.6800"))
        self.assertEqual(rows[1]["plan"], "Regular Plan")
        self.assertEqual(rows[1]["ter_pct"], Decimal("1.8200"))

    def test_hdfc_equity_portfolio_adapter_preserves_isin_and_weight(self):
        rows = parse_portfolio_rows(
            read_fixture("hdfc_equity_portfolio.tsv"),
            filename="hdfc_equity_portfolio.tsv",
            amc="HDFC Mutual Fund",
            portfolio_date=date(2026, 10, 31),
        )
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["isin"], "INE002A01018")
        self.assertEqual(rows[0]["instrument_type"], "EQUITY")
        self.assertEqual(rows[0]["pct_of_nav"], Decimal("8.2500"))

    def test_icici_debt_portfolio_adapter_preserves_rating_and_ytm(self):
        rows = parse_portfolio_rows(
            read_fixture("icici_debt_portfolio.tsv"),
            filename="icici_debt_portfolio.tsv",
            amc="ICICI Prudential Mutual Fund",
            portfolio_date=date(2026, 10, 31),
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["instrument_type"], "BOND")
        self.assertEqual(rows[0]["rating"], "AAA")
        self.assertEqual(rows[0]["ytm_pct"], Decimal("7.1200"))
        self.assertEqual(rows[0]["maturity_date"], date(2030, 8, 15))

    def test_sbi_hybrid_portfolio_adapter_keeps_cash_and_equity(self):
        rows = parse_portfolio_rows(
            read_fixture("sbi_hybrid_portfolio.tsv"),
            filename="sbi_hybrid_portfolio.tsv",
            amc="SBI Mutual Fund",
            portfolio_date=date(2026, 10, 31),
        )
        self.assertEqual({row["instrument_type"] for row in rows}, {"EQUITY", "CASH"})
        self.assertTrue(all(row["pct_of_nav"] is not None for row in rows))


class MasterDataPersistenceTests(TestCase):
    def setUp(self):
        self.scheme = AMFIMasterScheme.objects.create(
            scheme_code="120505",
            scheme_name="PWMS Balanced Hybrid Fund - Direct Plan - Growth",
            isin_growth="INF000A01003",
            plan="Direct Plan",
            option="Growth",
            is_growth_plan=True,
            sebi_universe_active=True,
        )

    def test_portfolio_reimport_is_idempotent_and_updates_existing_rows(self):
        rows = parse_portfolio_rows(
            read_fixture("hdfc_equity_portfolio.tsv"),
            filename="hdfc_equity_portfolio.tsv",
            amc="HDFC Mutual Fund",
            portfolio_date=date(2026, 10, 31),
        )
        first = persist_portfolio_rows(
            self.scheme, rows, portfolio_date=date(2026, 10, 31),
            source="AMC_PORTFOLIO", source_url="https://amc.example/portfolio",
        )
        second = persist_portfolio_rows(
            self.scheme, rows, portfolio_date=date(2026, 10, 31),
            source="AMC_PORTFOLIO", source_url="https://amc.example/portfolio",
        )
        self.assertEqual(first["inserted"], 2)
        self.assertEqual(second["updated"], 2)
        self.assertEqual(MFMasterHolding.objects.filter(scheme=self.scheme).count(), 2)

    def test_snapshot_upsert_does_not_create_duplicate_scheme_date(self):
        snapshot, created = MFMasterData.objects.update_or_create(
            scheme=self.scheme,
            as_of_date=date(2026, 10, 31),
            defaults={
                "scheme_code": self.scheme.scheme_code,
                "scheme_name": self.scheme.scheme_name,
                "field_sources": {
                    "scheme_name": {
                        "source": "AMFI",
                        "source_url": "https://portal.amfiindia.com/spages/NAVAll.txt",
                        "as_of_date": "2026-10-31",
                    }
                },
            },
        )
        self.assertTrue(created)
        second, created_again = MFMasterData.objects.update_or_create(
            scheme=self.scheme,
            as_of_date=date(2026, 10, 31),
            defaults={"scheme_name": "Updated scheme name"},
        )
        self.assertFalse(created_again)
        self.assertEqual(snapshot.pk, second.pk)
        self.assertEqual(MFMasterData.objects.filter(scheme=self.scheme).count(), 1)


class BenchmarkRatioTests(TestCase):
    def test_beta_alpha_and_r_squared_for_two_to_one_market_exposure(self):
        dates = [
            date(2026, 1, 1),
            date(2026, 1, 2),
            date(2026, 1, 3),
            date(2026, 1, 4),
            date(2026, 1, 5),
        ]
        fund_nav = [(dates[0], Decimal("100"))]
        benchmark_nav = [(dates[0], Decimal("100"))]
        fund_returns = [0.10, 0.20, -0.10, 0.05]
        benchmark_returns = [0.05, 0.10, -0.05, 0.025]
        for index, (fund_return, benchmark_return) in enumerate(zip(fund_returns, benchmark_returns), start=1):
            fund_nav.append((dates[index], fund_nav[-1][1] * Decimal(str(1 + fund_return))))
            benchmark_nav.append((dates[index], benchmark_nav[-1][1] * Decimal(str(1 + benchmark_return))))

        metrics = calculate_benchmark_relative_metrics(
            fund_nav, benchmark_nav, risk_free_rate=0.0,
        )
        self.assertAlmostEqual(metrics["beta"], 2.0, places=8)
        self.assertAlmostEqual(metrics["r_squared"], 1.0, places=8)
        self.assertAlmostEqual(metrics["alpha_pct"], 0.0, places=8)
        self.assertIsNotNone(metrics["tracking_error_pct"])
        self.assertIsNotNone(metrics["information_ratio"])

    def test_short_history_leaves_ratios_null_with_quality_flag(self):
        nav = [
            (date(2026, 1, 1), Decimal("100")),
            (date(2026, 1, 2), Decimal("101")),
        ]
        metrics = calculate_benchmark_relative_metrics(nav, nav, risk_free_rate=0.065)
        self.assertIsNone(metrics["beta"])
        self.assertIn("INSUFFICIENT_BENCHMARK_HISTORY", metrics["data_quality_flags"])
