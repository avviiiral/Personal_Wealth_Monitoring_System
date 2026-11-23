from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from mutual_funds.models import AMFIMasterScheme
from mutual_funds.master_models import MFExternalPortfolioSnapshot


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
