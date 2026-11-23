from io import StringIO
from unittest.mock import patch

from django.core.management import call_command, CommandError
from django.test import TestCase

from mutual_funds.models import AMFIMasterScheme
from mutual_funds.master_models import (
    MFExternalPortfolioSnapshot,
    MFMasterHolding,
    MFSourceRun,
)


class MFForecastBatchPlannerTests(TestCase):
    def create_scheme(self, code, amc, *, direct_growth=True, active=True):
        return AMFIMasterScheme.objects.create(
            scheme_code=str(code),
            scheme_name=f"{amc} Sample Fund {code}",
            amc_name=f"{amc} Mutual Fund" if amc else "",
            level_3_category="Hybrid",
            is_active=active,
            is_direct_plan_growth=direct_growth,
            is_growth_plan=direct_growth,
            sebi_universe_active=True,
        )

    def record_outcomes(self, *outcomes):
        MFSourceRun.objects.create(
            source="MFFORECAST_API",
            command="sync_mfforecast_holdings",
            status=MFSourceRun.Status.PARTIAL,
            metadata={"scheme_outcomes": list(outcomes)},
        )

    def test_plans_across_amcs_round_robin_and_skips_prior_results(self):
        hdfc_done = self.create_scheme("1001", "HDFC")
        self.create_scheme("1002", "HDFC")
        sbi_missing = self.create_scheme("2001", "SBI")
        self.create_scheme("2002", "SBI")
        self.create_scheme("3001", "ICICI")
        self.create_scheme("4001", "Axis")
        self.create_scheme("5001", "Nippon", direct_growth=False)

        MFExternalPortfolioSnapshot.objects.create(
            scheme=hdfc_done,
            provider=MFExternalPortfolioSnapshot.PROVIDER_MFFORECAST,
        )
        self.record_outcomes({
            "scheme_code": sbi_missing.scheme_code,
            "status": "NOT_FOUND",
        })

        output = StringIO()
        call_command("plan_mfforecast_batch", "--limit", "4", stdout=output)
        rendered = output.getvalue()

        self.assertIn("selected=4 | AMCs selected=4", rendered)
        self.assertIn("--scheme-code 1002", rendered)
        self.assertIn("--scheme-code 2002", rendered)
        self.assertIn("--scheme-code 3001", rendered)
        self.assertIn("--scheme-code 4001", rendered)
        self.assertNotIn("--scheme-code 1001", rendered)
        self.assertNotIn("--scheme-code 2001", rendered)
        self.assertNotIn("--scheme-code 5001", rendered)
        self.assertIn("--dry-run", rendered)

    def test_not_found_is_skipped_by_default_and_can_be_retried_explicitly(self):
        missing = self.create_scheme("6001", "Nippon")
        self.record_outcomes({
            "scheme_code": missing.scheme_code,
            "status": "NOT_FOUND",
        })

        default_output = StringIO()
        call_command(
            "plan_mfforecast_batch",
            "--amc", "Nippon",
            stdout=default_output,
        )
        self.assertIn("not_found=1", default_output.getvalue())
        self.assertIn("No new schemes are available", default_output.getvalue())

        retry_output = StringIO()
        call_command(
            "plan_mfforecast_batch",
            "--amc", "Nippon",
            "--retry-not-found",
            stdout=retry_output,
        )
        self.assertIn("--scheme-code 6001", retry_output.getvalue())

    def test_incomplete_and_error_outcomes_have_separate_retry_switches(self):
        incomplete = self.create_scheme("7001", "HDFC")
        failed = self.create_scheme("8001", "SBI")
        self.record_outcomes(
            {"scheme_code": incomplete.scheme_code, "status": "INCOMPLETE"},
            {"scheme_code": failed.scheme_code, "status": "ERROR"},
        )

        default_output = StringIO()
        call_command("plan_mfforecast_batch", "--limit", "50", stdout=default_output)
        self.assertIn("incomplete=1", default_output.getvalue())
        self.assertIn("errors=1", default_output.getvalue())
        self.assertNotIn("--scheme-code 7001", default_output.getvalue())
        self.assertNotIn("--scheme-code 8001", default_output.getvalue())

        retry_output = StringIO()
        call_command(
            "plan_mfforecast_batch",
            "--retry-incomplete",
            "--retry-errors",
            stdout=retry_output,
        )
        self.assertIn("--scheme-code 7001", retry_output.getvalue())
        self.assertIn("--scheme-code 8001", retry_output.getvalue())

    def test_unmapped_amc_scheme_is_excluded_and_reported(self):
        self.create_scheme("8101", "")
        mapped = self.create_scheme("8102", "HDFC")

        output = StringIO()
        call_command("plan_mfforecast_batch", "--amc", "HDFC", stdout=output)
        rendered = output.getvalue()

        self.assertIn("--scheme-code 8102", rendered)
        self.assertNotIn("--scheme-code 8101", rendered)
        self.assertIn("missing_amc=1", rendered)

    def test_limit_cannot_exceed_importer_maximum(self):
        with self.assertRaises(CommandError):
            call_command("plan_mfforecast_batch", "--limit", "51")


class MFForecastBatchOutcomeImportTests(TestCase):
    def create_scheme(self, code):
        return AMFIMasterScheme.objects.create(
            scheme_code=code,
            scheme_name=f"Sample Direct Growth Fund {code}",
            amc_name="Sample Mutual Fund",
            is_active=True,
            is_direct_plan_growth=True,
            is_growth_plan=True,
        )

    def run_import(self, scheme, fetch_result):
        output = StringIO()
        with patch(
            "mutual_funds.management.commands.sync_mfforecast_holdings.fetch_mfforecast_payload",
            return_value=fetch_result,
        ), patch.dict(
            "os.environ",
            {"MFFORECAST_API_KEY": "", "MFFORECAST_API_KEY_HEADER": ""},
        ):
            call_command(
                "sync_mfforecast_holdings",
                "--scheme-code", scheme.scheme_code,
                "--base-url", "https://api.example.test",
                stdout=output,
            )
        return output.getvalue(), MFSourceRun.objects.latest("started_at")

    def test_live_import_persists_404_outcome_without_creating_snapshot(self):
        scheme = self.create_scheme("9001")
        output, run = self.run_import(
            scheme,
            (None, "https://api.example.test/funds/9001/holdings", ""),
        )

        self.assertIn("not_found=1", output)
        self.assertEqual(run.status, MFSourceRun.Status.PARTIAL)
        self.assertEqual(
            run.metadata["scheme_outcomes"][0]["status"],
            "NOT_FOUND",
        )
        self.assertFalse(
            MFExternalPortfolioSnapshot.objects.filter(scheme=scheme).exists()
        )
        self.assertFalse(MFMasterHolding.objects.filter(scheme=scheme).exists())

    def test_live_import_persists_structurally_complete_outcome(self):
        scheme = self.create_scheme("9002")
        payload = {
            "amfi_code": "9002",
            "scheme_name": "Sample Direct Growth Fund",
            "holdings_count": 1,
            "holdings": [{
                "name": "Sample Listed Equity Ltd.",
                "isin": "INE000A01011",
                "asset_type": "EQUITY",
                "weight_pct": "100",
            }],
        }
        output, run = self.run_import(
            scheme,
            (payload, "https://api.example.test/funds/9002/holdings", ""),
        )

        self.assertIn("inserted=1", output)
        self.assertEqual(run.metadata["scheme_outcomes"][0]["status"], "IMPORTED")
        self.assertEqual(run.metadata["scheme_outcomes"][0]["holdings_count"], 1)
        self.assertEqual(
            MFExternalPortfolioSnapshot.objects.get(scheme=scheme).holdings_count,
            1,
        )
        self.assertEqual(MFMasterHolding.objects.filter(scheme=scheme).count(), 1)

    def test_live_import_persists_incomplete_count_mismatch(self):
        scheme = self.create_scheme("9003")
        payload = {
            "amfi_code": "9003",
            "scheme_name": "Sample Direct Growth Fund",
            "holdings_count": 2,
            "holdings": [{
                "name": "Sample Listed Equity Ltd.",
                "isin": "INE000A01012",
                "asset_type": "EQUITY",
                "weight_pct": "95",
            }],
        }
        _, run = self.run_import(
            scheme,
            (payload, "https://api.example.test/funds/9003/holdings", ""),
        )

        outcome = run.metadata["scheme_outcomes"][0]
        self.assertEqual(outcome["status"], "INCOMPLETE")
        self.assertEqual(outcome["provider_holdings_count"], 2)
        self.assertEqual(outcome["holdings_count"], 1)
        self.assertIn(
            "MFFORECAST_PROVIDER_HOLDINGS_COUNT_MISMATCH",
            outcome["quality_flags"],
        )
