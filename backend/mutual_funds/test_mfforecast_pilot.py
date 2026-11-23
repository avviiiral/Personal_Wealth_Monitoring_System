from io import StringIO

from django.core.management import call_command
from django.test import TestCase
from decimal import Decimal
from datetime import date

from mutual_funds.models import AMFIMasterScheme
from mutual_funds.master_models import (
    MFExternalPortfolioSnapshot,
    MFMasterHolding,
)


class MFForecastPilotPlannerTests(TestCase):
    def create_scheme(
        self,
        code,
        name,
        amc,
        category,
        *,
        direct_growth=True,
        sebi_active=True,
    ):
        return AMFIMasterScheme.objects.create(
            scheme_code=code,
            scheme_name=name,
            amc_name=amc,
            level_3_category=category,
            is_active=True,
            is_direct_plan_growth=direct_growth,
            is_growth_plan=direct_growth,
            sebi_universe_active=sebi_active,
        )

    def test_plans_one_unsynced_scheme_per_amc_and_prints_dry_run_command(self):
        previously_synced = self.create_scheme(
            "1001",
            "HDFC Banking and PSU Debt Fund - Direct Plan - Growth",
            "HDFC Mutual Fund",
            "Banking and PSU Debt",
        )
        hdfc_candidate = self.create_scheme(
            "1002",
            "HDFC Flexi Cap Fund - Direct Plan - Growth",
            "HDFC Mutual Fund",
            "Flexi Cap",
        )
        sbi_candidate = self.create_scheme(
            "2001",
            "SBI Small Cap Fund - Direct Plan - Growth",
            "SBI Mutual Fund",
            "Small Cap",
        )
        icici_candidate = self.create_scheme(
            "3001",
            "ICICI Prudential Corporate Bond Fund - Direct Plan - Growth",
            "ICICI Prudential Mutual Fund",
            "Corporate Bond",
        )
        axis_fallback = self.create_scheme(
            "4001",
            "Axis Midcap Fund - Direct Plan - Growth",
            "Axis Mutual Fund",
            "Mid Cap",
            sebi_active=False,
        )
        self.create_scheme(
            "5001",
            "Nippon India Regular Growth Fund",
            "Nippon India Mutual Fund",
            "Flexi Cap",
            direct_growth=False,
        )
        MFExternalPortfolioSnapshot.objects.create(
            scheme=previously_synced,
            provider=MFExternalPortfolioSnapshot.PROVIDER_MFFORECAST,
        )

        output = StringIO()
        call_command(
            "plan_mfforecast_pilot",
            "--amc", "HDFC",
            "--amc", "SBI",
            "--amc", "ICICI Prudential",
            "--amc", "Axis",
            stdout=output,
        )
        rendered = output.getvalue()

        self.assertIn(hdfc_candidate.scheme_code, rendered)
        self.assertIn(sbi_candidate.scheme_code, rendered)
        self.assertIn(icici_candidate.scheme_code, rendered)
        self.assertIn(axis_fallback.scheme_code, rendered)
        self.assertNotIn(previously_synced.scheme_code, rendered)
        self.assertIn("--scheme-code 1002", rendered)
        self.assertIn("--scheme-code 2001", rendered)
        self.assertIn("--scheme-code 3001", rendered)
        self.assertIn("--scheme-code 4001", rendered)
        self.assertIn("--dry-run", rendered)
        self.assertIn("No API requests were made", rendered)
        self.assertEqual(
            MFExternalPortfolioSnapshot.objects.filter(provider="MFFORECAST").count(),
            1,
        )

    def test_include_previously_synced_allows_existing_scheme(self):
        scheme = self.create_scheme(
            "9001",
            "HDFC Large Cap Fund - Direct Plan - Growth",
            "HDFC Mutual Fund",
            "Large Cap",
        )
        MFExternalPortfolioSnapshot.objects.create(
            scheme=scheme,
            provider=MFExternalPortfolioSnapshot.PROVIDER_MFFORECAST,
        )

        output = StringIO()
        call_command(
            "plan_mfforecast_pilot",
            "--amc", "HDFC",
            "--include-previously-synced",
            stdout=output,
        )

        self.assertIn("--scheme-code 9001", output.getvalue())


class MFForecastPersistedAuditTests(TestCase):
    def setUp(self):
        self.scheme = AMFIMasterScheme.objects.create(
            scheme_code="728001",
            scheme_name="SBI Corporate Bond Fund - Direct Plan - Growth",
            amc_name="SBI Mutual Fund",
            level_3_category="Corporate Bond",
            is_active=True,
            is_direct_plan_growth=True,
            is_growth_plan=True,
            sebi_universe_active=True,
        )

    def create_holding(self, code, weight, *, instrument_type="BOND"):
        return MFMasterHolding.objects.create(
            scheme=self.scheme,
            portfolio_date=date(2026, 9, 30),
            portfolio_date_basis=MFMasterHolding.DATE_BASIS_DISCLOSURE,
            security_name=f"Test security {code}",
            isin="INE000A0000" + str(code),
            security_key=f"ISIN:INE000A0000{code}",
            instrument_type=instrument_type,
            pct_of_nav=Decimal(weight),
            source="MFFORECAST_API",
            source_url="https://api.example.test/funds/728001/holdings",
        )

    def test_audit_reconciles_row_count_weight_and_reports_quality_warning(self):
        MFExternalPortfolioSnapshot.objects.create(
            scheme=self.scheme,
            provider=MFExternalPortfolioSnapshot.PROVIDER_MFFORECAST,
            provider_as_of_date=date(2026, 9, 30),
            holdings_count=2,
            total_weight_pct=Decimal("95.5500"),
            quality_flags=["MFFORECAST_HOLDING_WEIGHTS_NOT_100"],
        )
        self.create_holding("001", "50.0000")
        self.create_holding("002", "45.5500")

        output = StringIO()
        call_command(
            "audit_mfforecast_holdings",
            "--scheme-code", "728001",
            stdout=output,
        )
        rendered = output.getvalue()

        self.assertIn("WARN | 728001", rendered)
        self.assertIn("rows_snapshot=2 rows_stored=2", rendered)
        self.assertIn("weight_snapshot=95.5500 weight_stored=95.5500", rendered)
        self.assertIn("MFFORECAST_HOLDING_WEIGHTS_NOT_100", rendered)
        self.assertIn("Audit totals: schemes=1 pass=0 warn=1 fail=0.", rendered)

    def test_audit_fails_when_persisted_rows_do_not_match_snapshot(self):
        MFExternalPortfolioSnapshot.objects.create(
            scheme=self.scheme,
            provider=MFExternalPortfolioSnapshot.PROVIDER_MFFORECAST,
            provider_as_of_date=date(2026, 9, 30),
            holdings_count=2,
            total_weight_pct=Decimal("95.5500"),
            quality_flags=[],
        )
        self.create_holding("003", "95.5500")

        output = StringIO()
        call_command(
            "audit_mfforecast_holdings",
            "--scheme-code", "728001",
            stdout=output,
        )

        self.assertIn("FAIL | 728001", output.getvalue())
        self.assertIn("Audit totals: schemes=1 pass=0 warn=0 fail=1.", output.getvalue())
