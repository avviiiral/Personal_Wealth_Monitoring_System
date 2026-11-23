from datetime import date
from decimal import Decimal

from django.core.management import call_command
from django.test import TestCase

from mutual_funds.models import AMFIMasterScheme, AMFISchemeAAUM
from mutual_funds.master_models import MFMasterData, MFSourceRun


class BuildMFMasterDataCommandTests(TestCase):
    def setUp(self):
        self.scheme = AMFIMasterScheme.objects.create(
            scheme_code="990201",
            scheme_name="PWMS Builder Test Fund",
            isin_growth="INF990A01021",
            amc_name="PWMS Test AMC",
            plan="Direct Plan",
            option="Growth",
            is_growth_plan=True,
            sebi_universe_active=True,
            level_1_branch="Equity",
            level_2_group="By Market Cap",
            level_3_category="Large Cap",
        )
        MFMasterData.objects.create(
            scheme=self.scheme,
            as_of_date=date(2026, 9, 30),
            scheme_code=self.scheme.scheme_code,
            scheme_name=self.scheme.scheme_name,
            amc=self.scheme.amc_name,
            ter_direct_pct=Decimal("0.6800"),
            ter_as_of=date(2026, 9, 30),
            field_sources={
                "ter_direct_pct": {
                    "source": "AMFI_TER",
                    "source_url": "https://www.amfiindia.com/ter-of-mf-schemes",
                    "as_of_date": "2026-09-30",
                    "fetched_at": "2026-09-30T12:00:00+00:00",
                }
            },
        )
        AMFISchemeAAUM.objects.create(
            scheme=self.scheme,
            quarter_start=date(2026, 7, 1),
            quarter_label="July - September 2026",
            average_aum_cr=Decimal("100.2500"),
            source="AMFI_SCHEME_AAUM",
            source_scheme_name=self.scheme.scheme_name,
        )

    def test_builder_carries_forward_ter_and_selects_quarterly_aum_type(self):
        call_command("build_mf_masterdata", as_of="2026-10-31")
        snapshot = MFMasterData.objects.get(scheme=self.scheme, as_of_date=date(2026, 10, 31))
        self.assertEqual(snapshot.ter_direct_pct, Decimal("0.6800"))
        self.assertEqual(snapshot.aum_cr, Decimal("100.2500"))
        self.assertEqual(snapshot.aum_type, "QUARTERLY_AVG")
        self.assertEqual(snapshot.aum_as_of, date(2026, 9, 30))
        self.assertEqual(snapshot.field_sources["ter_direct_pct"]["source"], "AMFI_TER")
        self.assertEqual(snapshot.field_sources["aum_cr"]["source"], "AMFI_SCHEME_AAUM")
        self.assertIn("NAV_HISTORY_NOT_AVAILABLE", snapshot.data_quality_flags)

    def test_month_end_aum_is_preferred_when_its_observation_is_newer(self):
        MFMasterData.objects.create(
            scheme=self.scheme,
            as_of_date=date(2026, 10, 31),
            scheme_code=self.scheme.scheme_code,
            scheme_name=self.scheme.scheme_name,
            aum_cr=Decimal("115.5000"),
            aum_type="MONTH_END",
            aum_as_of=date(2026, 10, 31),
            aum_source="AMC_MONTH_END_AUM",
            field_sources={
                "aum_cr": {
                    "source": "AMC_MONTH_END_AUM",
                    "source_url": "https://amc.example/month-end-aum",
                    "as_of_date": "2026-10-31",
                }
            },
        )
        call_command("build_mf_masterdata", as_of="2026-11-01")
        snapshot = MFMasterData.objects.get(scheme=self.scheme, as_of_date=date(2026, 11, 1))
        self.assertEqual(snapshot.aum_cr, Decimal("115.5000"))
        self.assertEqual(snapshot.aum_type, "MONTH_END")
        self.assertEqual(snapshot.aum_as_of, date(2026, 10, 31))
        self.assertEqual(snapshot.field_sources["aum_cr"]["source"], "AMC_MONTH_END_AUM")

    def test_dry_run_does_not_write_snapshot_or_source_run(self):
        call_command("build_mf_masterdata", as_of="2026-10-31", dry_run=True)
        self.assertFalse(
            MFMasterData.objects.filter(scheme=self.scheme, as_of_date=date(2026, 10, 31)).exists()
        )
        self.assertFalse(MFSourceRun.objects.filter(command="build_mf_masterdata").exists())

    def test_rebuild_is_idempotent_for_scheme_and_snapshot_date(self):
        call_command("build_mf_masterdata", as_of="2026-10-31")
        call_command("build_mf_masterdata", as_of="2026-10-31")
        self.assertEqual(
            MFMasterData.objects.filter(scheme=self.scheme, as_of_date=date(2026, 10, 31)).count(),
            1,
        )
