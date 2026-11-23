"""Database tests for the masterdata ingestion jobs (no live HTTP).

Network access is injected: every job accepts a fetch/client callable so the
tests feed it saved AMFI/mfapi fixtures.
"""
import io
import shutil
import sqlite3
import tempfile
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pandas as pd
from django.test import TestCase
from django.utils import timezone

from mutual_funds.master_models import (
    MFAMFIRiskParameter, MFMasterData, MFMasterHolding, MFRiskFreeRate,
    MFSchemeIdentityMap, MFSourceRun,
)
from mutual_funds.models import (
    AMFIMasterNAV, AMFIMasterScheme, AMFISchemeAAUM, MutualFundHolding,
    MutualFundScheme,
)
from mutual_funds.services.mf_pipeline import identity, jobs
from mutual_funds.test_mf_pipeline_parsers import FIXTURES, fixture_json, fixture_text

MASTERDATA_FIXTURES = FIXTURES.parent / "masterdata"
DAY1 = fixture_text("navall_midcap_2026-11-20.txt")
DAY2 = fixture_text("navall_midcap_2026-11-23_axis_missing.txt")
SCHEME_DATA = fixture_text("amfi_scheme_data_midcap.csv")


def ter_xlsx(blank_hdfc_regular=False):
    frame = pd.read_csv(FIXTURES / "amfi_ter_export_2026-10.csv", dtype=object)
    if blank_hdfc_regular:
        mask = frame["Scheme Name"] == "HDFC Mid Cap Fund"
        frame.loc[mask, "Regular Plan - Total TER (%)"] = None
        frame.loc[mask, "Regular Plan - Base Expense Ratio (BER) (%)"] = None
    buffer = io.BytesIO()
    frame.to_excel(buffer, index=False)
    return buffer.getvalue()


class FakeRiskClient:
    def __init__(self, by_category):
        self.by_category = by_category
        self.calls = []

    def fetch(self, date, category_id):
        self.calls.append((date, str(category_id)))
        return self.by_category.get(str(category_id), [])


class FakeAumClient:
    def __init__(self):
        self.periods_json = fixture_json("amfi_aaum_periods_fy2026-27.json")

    def financial_years(self):
        return self.periods_json["years"][:2]

    def periods(self, fy_id):
        return self.periods_json["data"] if fy_id == 1 else {"periods": []}

    def average_aum_schemewise(self, fy_id, period_id):
        # Jul-Sep (period 1) not yet published in this fake; Apr-Jun is.
        if period_id == 2:
            return fixture_json("amfi_aaum_schemewise_apr-jun-2026.json")["data"]
        return []


class PipelineTestCase(TestCase):
    def load_universe(self):
        run = jobs.sync_nav(fetch_text=lambda: DAY1)
        self.assertEqual(run.status, MFSourceRun.Status.SUCCESS, run.errors)
        return run


class NavSyncTests(PipelineTestCase):
    def test_loads_schemes_and_nav_into_existing_tables(self):
        run = self.load_universe()

        self.assertEqual(AMFIMasterScheme.objects.count(), 7)
        hdfc = AMFIMasterScheme.objects.get(scheme_code="118989")
        self.assertEqual(hdfc.amc_name, "HDFC Mutual Fund")
        self.assertEqual(hdfc.raw_category_header, "Open Ended Schemes(Equity Scheme - Mid Cap Fund)")
        self.assertEqual(hdfc.isin_growth, "INF179K01XQ0")
        nav = AMFIMasterNAV.objects.get(scheme=hdfc)
        self.assertEqual((nav.date, nav.nav, nav.source), (date(2026, 11, 20), Decimal("232.517000"), "AMFI_NAVALL"))
        self.assertEqual(run.command, "mf_sync_nav")
        self.assertEqual(len(run.source_file_hash), 64)
        self.assertEqual(run.rows_inserted, 7)

    def test_rerun_with_same_file_is_skipped_and_does_not_duplicate(self):
        self.load_universe()
        rerun = jobs.sync_nav(fetch_text=lambda: DAY1)

        self.assertEqual(rerun.status, MFSourceRun.Status.SKIPPED)
        self.assertEqual(rerun.metadata["skip_reason"], "UNCHANGED_FILE_HASH")
        self.assertEqual(AMFIMasterNAV.objects.count(), 7)

    def test_new_file_adds_one_nav_per_scheme_per_date(self):
        self.load_universe()
        jobs.sync_nav(fetch_text=lambda: DAY2)
        hdfc_navs = AMFIMasterNAV.objects.filter(scheme__scheme_code="118989").order_by("date")
        self.assertEqual([n.date for n in hdfc_navs], [date(2026, 11, 20), date(2026, 11, 23)])
        self.assertEqual(AMFIMasterScheme.objects.count(), 7)  # nothing deleted

    def test_scheme_missing_more_than_seven_days_is_marked_inactive_not_deleted(self):
        self.load_universe()
        jobs.sync_nav(fetch_text=lambda: DAY2)
        today = timezone.localdate()
        AMFIMasterScheme.objects.filter(scheme_code__in=["120505", "114564"]).update(
            universe_last_seen_at=timezone.now() - timedelta(days=8),
        )
        changed = jobs.mark_navall_inactive(today=today)

        self.assertEqual(changed["deactivated"], 2)
        axis = AMFIMasterScheme.objects.get(scheme_code="120505")
        self.assertFalse(axis.navall_active)
        self.assertEqual(axis.navall_inactive_since, today)
        self.assertTrue(axis.is_active)  # pre-existing flag untouched
        self.assertTrue(AMFIMasterScheme.objects.get(scheme_code="118989").navall_active)

        # Reappearing in NAVAll reactivates it.
        jobs.sync_nav(fetch_text=lambda: DAY1)
        axis.refresh_from_db()
        self.assertTrue(axis.navall_active)
        self.assertIsNone(axis.navall_inactive_since)

    def test_scheme_missing_less_than_seven_days_stays_active(self):
        self.load_universe()
        AMFIMasterScheme.objects.filter(scheme_code="120505").update(
            universe_last_seen_at=timezone.now() - timedelta(days=6),
        )
        jobs.mark_navall_inactive(today=timezone.localdate())
        self.assertTrue(AMFIMasterScheme.objects.get(scheme_code="120505").navall_active)

    def test_download_failure_is_logged_as_failed(self):
        def boom():
            raise RuntimeError("AMFI unavailable")

        run = jobs.sync_nav(fetch_text=boom)
        self.assertEqual(run.status, MFSourceRun.Status.FAILED)
        self.assertIn("AMFI unavailable", run.errors[0]["error"])
        self.assertIsNotNone(run.completed_at)


class IdentityResolverTests(PipelineTestCase):
    def test_parent_name_resolves_all_plan_codes_once(self):
        self.load_universe()
        resolver = identity.IdentityResolver.from_scheme_data_text(SCHEME_DATA)
        schemes = resolver.resolve(MFSchemeIdentityMap.SOURCE_TER_NSDL, "HDFC/O/E/MIF/07/04/0015", "HDFC Mid Cap Fund")
        self.assertEqual(sorted(s.scheme_code for s in schemes), ["105758", "118989"])

        resolver.resolve(MFSchemeIdentityMap.SOURCE_TER_NSDL, "HDFC/O/E/MIF/07/04/0015", "HDFC Mid Cap Fund")
        self.assertEqual(MFSchemeIdentityMap.objects.filter(external_id="HDFC/O/E/MIF/07/04/0015").count(), 2)

    def test_stored_map_wins_over_name_and_rejection_blocks(self):
        self.load_universe()
        hdfc = AMFIMasterScheme.objects.get(scheme_code="118989")
        MFSchemeIdentityMap.objects.create(
            source=MFSchemeIdentityMap.SOURCE_RISK_SCHEME_ID, external_id="2526",
            scheme=hdfc, match_method=MFSchemeIdentityMap.METHOD_MANUAL,
        )
        resolver = identity.IdentityResolver.from_scheme_data_text(SCHEME_DATA)
        resolved = resolver.resolve(MFSchemeIdentityMap.SOURCE_RISK_SCHEME_ID, "2526", "HDFC Mid Cap Fund")
        self.assertEqual([s.scheme_code for s in resolved], ["118989"])

        MFSchemeIdentityMap.objects.filter(external_id="2526").update(status=MFSchemeIdentityMap.STATUS_REJECTED)
        self.assertEqual(resolver.resolve(MFSchemeIdentityMap.SOURCE_RISK_SCHEME_ID, "2526", "HDFC Mid Cap Fund"), [])

    def test_parent_name_across_two_amcs_is_ambiguous(self):
        self.load_universe()
        text = SCHEME_DATA + "Other AMC Limited,999001,HDFC Mid Cap Fund,Open Ended,Equity Scheme - Mid Cap Fund,X,5000,01-Jan-2020,01-Jan-2020,INF000X01001\n"
        AMFIMasterScheme.objects.create(scheme_code="999001", scheme_name="HDFC Mid Cap Fund", amc_name="Other Mutual Fund")
        resolver = identity.IdentityResolver.from_scheme_data_text(text)
        self.assertEqual(resolver.resolve(MFSchemeIdentityMap.SOURCE_TER_NSDL, "X1", "HDFC Mid Cap Fund"), [])
        self.assertFalse(MFSchemeIdentityMap.objects.filter(external_id="X1").exists())

    def test_unknown_name_returns_nothing(self):
        self.load_universe()
        resolver = identity.IdentityResolver.from_scheme_data_text(SCHEME_DATA)
        self.assertEqual(resolver.resolve(MFSchemeIdentityMap.SOURCE_TER_NSDL, "Z", "No Such Fund"), [])


class TerSyncTests(PipelineTestCase):
    def run_ter(self, payload=None):
        return jobs.sync_ter(
            month="10-2026",
            fetch_excel=lambda month: payload or ter_xlsx(),
            fetch_scheme_data=lambda: SCHEME_DATA,
        )

    def test_ter_applied_to_every_plan_of_the_parent_scheme(self):
        self.load_universe()
        run = self.run_ter()

        self.assertEqual(run.status, MFSourceRun.Status.SUCCESS, run.errors)
        for code in ("118989", "105758"):
            snap = MFMasterData.objects.filter(scheme__scheme_code=code).latest("as_of_date")
            self.assertEqual(snap.ter_direct_pct, Decimal("0.7400"))
            self.assertEqual(snap.ter_regular_pct, Decimal("1.3100"))
            self.assertEqual(snap.ter_as_of, date(2026, 10, 31))
            self.assertEqual(snap.ter_source, "AMFI_TER")
            self.assertEqual(snap.field_sources["ter_direct_pct"]["source"], "AMFI_TER")

    def test_rerun_same_file_skipped_and_no_duplicate_rows(self):
        self.load_universe()
        self.run_ter()
        snapshots = MFMasterData.objects.count()
        rerun = self.run_ter()
        self.assertEqual(rerun.status, MFSourceRun.Status.SKIPPED)
        self.assertEqual(MFMasterData.objects.count(), snapshots)

    def test_missing_field_does_not_overwrite_existing_value_with_null(self):
        self.load_universe()
        self.run_ter()
        self.run_ter(ter_xlsx(blank_hdfc_regular=True))
        snap = MFMasterData.objects.filter(scheme__scheme_code="118989").latest("as_of_date")
        self.assertEqual(snap.ter_regular_pct, Decimal("1.3100"))
        self.assertEqual(snap.ter_direct_pct, Decimal("0.7400"))

    def test_disclosure_lands_on_latest_snapshot_so_builder_carries_it(self):
        self.load_universe()
        hdfc = AMFIMasterScheme.objects.get(scheme_code="118989")
        later = MFMasterData.objects.create(scheme=hdfc, as_of_date=date(2026, 11, 20), scheme_code="118989")
        self.run_ter()
        later.refresh_from_db()
        self.assertEqual(later.ter_direct_pct, Decimal("0.7400"))
        self.assertFalse(MFMasterData.objects.filter(scheme=hdfc, as_of_date=date(2026, 10, 31)).exists())


class AaumSyncTests(PipelineTestCase):
    def test_latest_published_quarter_is_loaded_by_amfi_code(self):
        self.load_universe()
        run = jobs.sync_aum(aum_client=FakeAumClient())

        self.assertEqual(run.status, MFSourceRun.Status.SUCCESS, run.errors)
        row = AMFISchemeAAUM.objects.get(scheme__scheme_code="118989")
        self.assertEqual(row.quarter_start, date(2026, 4, 1))
        self.assertEqual(row.quarter_label, "April - June 2026")
        self.assertEqual(row.average_aum_cr, Decimal("26843.5866"))
        self.assertEqual(row.source, "AMFI_SCHEME_AAUM")
        self.assertEqual(run.metadata["unmatched_codes"], 1)  # FoF code not in fixture universe

    def test_rerun_for_loaded_period_is_skipped(self):
        self.load_universe()
        jobs.sync_aum(aum_client=FakeAumClient())
        rerun = jobs.sync_aum(aum_client=FakeAumClient())
        self.assertEqual(rerun.status, MFSourceRun.Status.SKIPPED)
        self.assertEqual(rerun.metadata["skip_reason"], "PERIOD_ALREADY_LOADED")
        self.assertEqual(AMFISchemeAAUM.objects.count(), 7)  # 7 universe codes; FoF code unmatched


class AmfiRiskSyncTests(PipelineTestCase):
    def test_risk_parameters_stored_raw_and_projected_onto_masterdata(self):
        self.load_universe()
        client = FakeRiskClient({"17": fixture_json("amfi_risk_parameters_cat17_2026-10.json")})
        run = jobs.sync_amfi_risk(
            report_month=date(2026, 10, 1), risk_client=client,
            fetch_scheme_data=lambda: SCHEME_DATA, categories=("17", "18"),
        )

        self.assertEqual(run.status, MFSourceRun.Status.SUCCESS, run.errors)
        self.assertEqual(client.calls, [("01-Oct-2026", "17"), ("01-Oct-2026", "18")])
        self.assertEqual(MFAMFIRiskParameter.objects.count(), 3)
        snap = MFMasterData.objects.filter(scheme__scheme_code="118989").latest("as_of_date")
        self.assertEqual(snap.amfi_std_dev_pct, Decimal("16.0800"))
        self.assertEqual(snap.amfi_benchmark_std_dev_pct, Decimal("18.5800"))
        self.assertEqual(snap.amfi_beta, Decimal("0.8221"))
        self.assertIsNone(snap.amfi_sharpe)
        self.assertEqual(snap.amfi_risk_as_of, date(2026, 10, 1))
        absl = MFMasterData.objects.filter(scheme__scheme_code="119619").latest("as_of_date")
        self.assertEqual(absl.amfi_beta, Decimal("0.9600"))

    def test_not_yet_published_is_skipped_and_retried_later(self):
        self.load_universe()
        run = jobs.sync_amfi_risk(
            report_month=date(2026, 11, 1), risk_client=FakeRiskClient({}),
            fetch_scheme_data=lambda: SCHEME_DATA,
        )
        self.assertEqual(run.status, MFSourceRun.Status.SKIPPED)
        self.assertEqual(run.metadata["skip_reason"], "NOT_YET_AVAILABLE")

    def test_loaded_month_is_not_fetched_again(self):
        self.load_universe()
        client = FakeRiskClient({"17": fixture_json("amfi_risk_parameters_cat17_2026-10.json")})
        kwargs = dict(report_month=date(2026, 10, 1), risk_client=client, fetch_scheme_data=lambda: SCHEME_DATA, categories=("17",))
        jobs.sync_amfi_risk(**kwargs)
        rerun = jobs.sync_amfi_risk(**kwargs)
        self.assertEqual(rerun.metadata["skip_reason"], "PERIOD_ALREADY_LOADED")
        self.assertEqual(len(client.calls), 1)


class NavHistoryTests(PipelineTestCase):
    def test_mfapi_gap_fill_inserts_only_missing_dates(self):
        self.load_universe()
        payload = fixture_json("mfapi_118989_2026-11.json")
        run = jobs.fill_nav_from_mfapi(scheme_codes=["118989"], fetch_json=lambda code, start, end: payload)

        self.assertEqual(run.rows_inserted, 5)  # 20-Nov already present from NAVAll
        dates = list(AMFIMasterNAV.objects.filter(scheme__scheme_code="118989").order_by("date").values_list("date", "source"))
        self.assertEqual(dates[0], (date(2026, 11, 13), "mfapi.in"))
        self.assertEqual(dates[-1], (date(2026, 11, 20), "AMFI_NAVALL"))

        rerun = jobs.fill_nav_from_mfapi(scheme_codes=["118989"], fetch_json=lambda code, start, end: payload)
        self.assertEqual(rerun.rows_inserted, 0)

    def test_mfapi_isin_mismatch_is_rejected(self):
        self.load_universe()
        payload = fixture_json("mfapi_118989_2026-11.json")
        payload["meta"]["isin_growth"] = "INF000X00000"
        run = jobs.fill_nav_from_mfapi(scheme_codes=["118989"], fetch_json=lambda code, start, end: payload)
        self.assertEqual(run.rows_inserted, 0)
        self.assertEqual(run.rows_failed, 1)

    def _funds_db(self, table):
        directory = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, directory)
        path = Path(directory) / "funds.db"
        con = sqlite3.connect(path)
        con.execute("CREATE TABLE schemes (scheme_code INTEGER, scheme_name TEXT)")
        con.execute(f"CREATE TABLE {table} (date TEXT, scheme_code INTEGER, nav FLOAT)")
        con.execute("CREATE TABLE securities (isin TEXT, type INTEGER, scheme_code INTEGER)")
        con.executemany(f"INSERT INTO {table} VALUES (?,?,?)", [
            ("2026-11-18", 118989, 228.631), ("2026-11-19", 118989, 230.39),
            ("2026-11-20", 118989, 232.517), ("2026-11-19", 105758, "#N/A"),
            ("2026-11-19", 119620, 949.0),
        ])
        con.executemany("INSERT INTO securities VALUES (?,?,?)", [
            ("INF179K01XQ0", 0, 118989), ("INF999Z01ZZ9", 0, 119620),
        ])
        con.commit()
        con.close()
        return path

    def test_archive_backfill_reads_nav_table_and_skips_existing_dates(self):
        self.load_universe()
        run = jobs.backfill_nav_from_archive(db_path=self._funds_db("nav"))
        self.assertEqual(run.status, MFSourceRun.Status.SUCCESS, run.errors)
        self.assertEqual(run.rows_inserted, 2)  # 18th + 19th for 118989; invalid NAV ignored
        self.assertEqual(run.metadata["isin_mismatch_schemes"], ["119620"])
        self.assertEqual(
            AMFIMasterNAV.objects.filter(scheme__scheme_code="118989", source="AMFI_HIST_ARCHIVE").count(), 2,
        )

    def test_archive_backfill_supports_funds_table_name(self):
        self.load_universe()
        run = jobs.backfill_nav_from_archive(db_path=self._funds_db("funds"))
        self.assertEqual(run.rows_inserted, 2)

    def test_archive_without_nav_table_fails_clearly(self):
        directory = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, directory)
        path = Path(directory) / "empty.db"
        sqlite3.connect(path).close()
        run = jobs.backfill_nav_from_archive(db_path=path)
        self.assertEqual(run.status, MFSourceRun.Status.FAILED)


class HoldingsSyncTests(PipelineTestCase):
    def setUp(self):
        self.inbox = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.inbox)

    def hold(self, isin):
        scheme = MutualFundScheme.objects.create(scheme_name="held", isin_growth=isin)
        MutualFundHolding.objects.create(scheme=scheme, units=Decimal("10"))

    def test_only_held_amcs_are_processed_and_unsupported_ones_logged(self):
        self.load_universe()
        self.hold("INF179K01XQ0")  # HDFC (parser exists)
        self.hold("INF846K01EH3")  # Axis (no parser yet)
        target = self.inbox / "hdfc"
        target.mkdir()
        shutil.copy(MASTERDATA_FIXTURES / "hdfc_equity_portfolio.tsv", target / "2026-10-31__118989__hdfc-midcap.tsv")

        run = jobs.sync_holdings(inbox_dir=self.inbox, config_path=self.inbox / "none.json")

        self.assertEqual(run.status, MFSourceRun.Status.PARTIAL, run.errors)
        self.assertEqual(run.metadata["held_amcs"], ["Axis Mutual Fund", "HDFC Mutual Fund"])
        self.assertEqual(run.metadata["unsupported_amcs"], ["Axis Mutual Fund"])
        holdings = MFMasterHolding.objects.filter(scheme__scheme_code="118989", portfolio_date=date(2026, 10, 31))
        self.assertEqual(holdings.count(), 2)
        snap = MFMasterData.objects.filter(scheme__scheme_code="118989").latest("as_of_date")
        self.assertEqual(snap.holdings_as_of, date(2026, 10, 31))
        self.assertEqual(snap.top10_holdings[0]["security_isin"], "INE002A01018")
        self.assertEqual(snap.top10_holdings[0]["pct_of_nav"], "8.2500")

        rerun = jobs.sync_holdings(inbox_dir=self.inbox, config_path=self.inbox / "none.json")
        self.assertEqual(rerun.metadata["files_skipped_unchanged"], 1)
        self.assertEqual(MFMasterHolding.objects.count(), 2)

    def test_no_holdings_means_nothing_to_do(self):
        self.load_universe()
        run = jobs.sync_holdings(inbox_dir=self.inbox, config_path=self.inbox / "none.json")
        self.assertEqual(run.status, MFSourceRun.Status.SKIPPED)
        self.assertEqual(run.metadata["skip_reason"], "NO_HELD_SCHEMES")


class RiskFreeSyncTests(PipelineTestCase):
    def test_inbox_file_loaded_once(self):
        inbox = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, inbox)
        (inbox / "tbill.csv").write_text("Date,Yield\n2026-11-19,6.85\n")
        run = jobs.sync_risk_free(inbox_dir=inbox, url="")
        self.assertEqual(run.status, MFSourceRun.Status.SUCCESS, run.errors)
        self.assertEqual(MFRiskFreeRate.objects.get().rate_pct, Decimal("6.8500"))
        again = jobs.sync_risk_free(inbox_dir=inbox, url="")
        self.assertEqual(again.status, MFSourceRun.Status.SKIPPED)
        self.assertEqual(MFRiskFreeRate.objects.count(), 1)

    def test_not_configured_is_skipped(self):
        empty = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, empty)
        run = jobs.sync_risk_free(inbox_dir=empty, url="")
        self.assertEqual(run.metadata["skip_reason"], "NOT_CONFIGURED")


class CalcRatiosTests(PipelineTestCase):
    def setUp(self):
        self.load_universe()
        self.scheme = AMFIMasterScheme.objects.get(scheme_code="118989")
        day, value, rows = date(2023, 6, 1), 100.0, []
        index = 0
        while day <= date(2026, 11, 20):
            if day.weekday() < 5:
                value *= 1.0007 if index % 4 else 0.9985
                rows.append(AMFIMasterNAV(scheme=self.scheme, date=day, nav=round(value, 4), source="mfapi.in"))
                index += 1
            day += timedelta(days=1)
        AMFIMasterNAV.objects.bulk_create(rows, ignore_conflicts=True)

    def test_calculated_ratios_and_latest_nav_written_to_snapshot(self):
        run = jobs.calc_ratios(as_of=date(2026, 11, 20), scheme_codes=["118989"])

        self.assertEqual(run.status, MFSourceRun.Status.SUCCESS, run.errors)
        snap = MFMasterData.objects.get(scheme=self.scheme, as_of_date=date(2026, 11, 20))
        self.assertIsNotNone(snap.calc_sharpe_1y)
        self.assertIsNotNone(snap.calc_std_dev_3y_pct)
        self.assertIsNone(snap.calc_beta_1y)  # no benchmark TRI loaded
        self.assertEqual(snap.calc_risk_free_rate_pct, Decimal("6.5000"))
        self.assertIn("RISK_FREE_RATE_DEFAULT_USED", snap.data_quality_flags)
        self.assertEqual(snap.nav, Decimal("232.5170"))  # official NAVAll wins on its date
        self.assertEqual(snap.nav_as_of, date(2026, 11, 20))
        self.assertEqual(snap.nav_source, "AMFI_NAVALL")
        self.assertEqual(snap.calc_ratios_as_of, date(2026, 11, 20))
        self.assertIsNotNone(snap.sharpe)  # existing builder columns still populated

    def test_stored_risk_free_rate_is_used(self):
        MFRiskFreeRate.objects.create(date=date(2026, 11, 19), rate_pct=Decimal("7.0"), source="TEST")
        jobs.calc_ratios(as_of=date(2026, 11, 20), scheme_codes=["118989"])
        snap = MFMasterData.objects.get(scheme=self.scheme, as_of_date=date(2026, 11, 20))
        self.assertEqual(snap.calc_risk_free_rate_pct, Decimal("7.0000"))
        self.assertNotIn("RISK_FREE_RATE_DEFAULT_USED", snap.data_quality_flags)

    def test_same_nav_date_is_not_recalculated(self):
        jobs.calc_ratios(as_of=date(2026, 11, 20), scheme_codes=["118989"])
        rerun = jobs.calc_ratios(as_of=date(2026, 11, 20), scheme_codes=["118989"])
        self.assertEqual(rerun.status, MFSourceRun.Status.SKIPPED)
        self.assertEqual(rerun.metadata["skip_reason"], "ALREADY_CALCULATED_FOR_NAV_DATE")
