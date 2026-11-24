from datetime import timedelta
from unittest import mock

from django.test import TestCase
from django.utils import timezone

from mf_intelligence.models import (
    MFAumHistory,
    MFBenchmark,
    MFBenchmarkMonthly,
    MFCategory,
    MFCategoryMedian,
    MFHolding,
    MFHoldingSnapshot,
    MFNavMonthly,
    MFScheme,
    MFSchemeMetric,
    MFStockClassification,
    MFSyncRun,
)
from mf_intelligence.services import cap_list
from mf_intelligence.services.sync import STEP_AUM, STEP_BENCHMARKS, STEP_HOLDINGS, STEP_NAV, MFIntelligenceSync
from mf_intelligence.tests.fakes import (
    FUND_A,
    FUND_A_DIRECT,
    FUND_A_IDCW,
    FUND_B,
    FUND_UNCLAIMED,
    LIQUID_FUND,
    FakeCharlieAlpha,
)

FUND_STEPS = (STEP_NAV, STEP_HOLDINGS, STEP_AUM, STEP_BENCHMARKS)
HOLDINGS_REFRESH_DAYS = 7  # pwms_config mf_intelligence.holdings_refresh_days
STALE_RUN_HOURS = 6  # pwms_config mf_intelligence.stale_run_hours
HNAV_TER = "1.5000"  # FakeCharlieAlpha.nav_history "ter"
FAKE_EQUITY_PCT = "95.0000"  # FakeCharlieAlpha.holdings equity allocation


class SyncTests(TestCase):
    def setUp(self):
        self.client_api = FakeCharlieAlpha()

    def sync(self, **kwargs):
        kwargs.setdefault("steps", FUND_STEPS)
        return MFIntelligenceSync(client=self.client_api, workers=2).run(**kwargs)

    def test_full_category_sync_persists_every_dataset(self):
        run = self.sync(categories=["multi-cap"])

        self.assertEqual(run.status, MFSyncRun.STATUS_SUCCESS)
        self.assertEqual(MFCategory.objects.count(), 2)
        self.assertEqual(MFScheme.objects.count(), 6)
        self.assertEqual(MFSchemeMetric.objects.filter(scheme__amfi_code=FUND_A).count(), 4)
        self.assertEqual(MFCategoryMedian.objects.filter(category__slug="multi-cap").count(), 4)

        fund = MFScheme.objects.get(amfi_code=FUND_A)
        self.assertEqual(str(fund.ter), HNAV_TER)  # hnav value overrides the metrics row
        self.assertIsNotNone(fund.return_1y)
        self.assertIsNotNone(fund.return_5y)
        self.assertIn("2024", fund.calendar_returns)
        self.assertEqual(fund.isin, f"INF{FUND_A}XX01")
        self.assertTrue(MFNavMonthly.objects.filter(scheme=fund).exists())
        self.assertEqual(MFAumHistory.objects.filter(scheme=fund).count(), 2)

        snapshot = MFHoldingSnapshot.objects.get(scheme=fund)
        self.assertEqual(str(snapshot.equity_pct), FAKE_EQUITY_PCT)
        self.assertEqual(snapshot.complete_exits_count, 1)
        self.assertEqual(MFHolding.objects.filter(snapshot=snapshot, is_exit=True).count(), 1)
        self.assertEqual(MFHolding.objects.filter(snapshot=snapshot, is_exit=False).count(), 4)

        self.assertEqual(MFBenchmark.objects.exclude(latest_date__isnull=True).count(), 2)
        self.assertTrue(MFBenchmarkMonthly.objects.exists())

    def test_only_growth_plans_of_selected_plan_are_fetched(self):
        self.sync(categories=["multi-cap"])
        fetched = {call[1] for call in self.client_api.calls_named("nav")}
        self.assertEqual(fetched, {FUND_A, FUND_B})
        for skipped in (FUND_A_DIRECT, FUND_A_IDCW, FUND_UNCLAIMED, LIQUID_FUND):
            self.assertNotIn(skipped, fetched)

    def test_plan_both_fetches_regular_and_direct_growth_plans(self):
        self.sync(categories=["multi-cap"], plans=["Regular", "Direct"])
        fetched = {call[1] for call in self.client_api.calls_named("nav")}
        self.assertEqual(fetched, {FUND_A, FUND_A_DIRECT, FUND_B})

    def test_second_run_is_idempotent_and_skips_fresh_data(self):
        self.sync(categories=["multi-cap"])
        counts = {
            model: model.objects.count()
            for model in (MFScheme, MFSchemeMetric, MFNavMonthly, MFHoldingSnapshot, MFHolding, MFAumHistory, MFBenchmarkMonthly)
        }
        self.client_api.calls.clear()
        self.sync(categories=["multi-cap"])

        for model, count in counts.items():
            self.assertEqual(model.objects.count(), count, model.__name__)
        # NAV synced today, holdings this week, AUM this month, benchmarks today.
        for name in ("nav", "holdings", "aum", "benchmark"):
            self.assertEqual(self.client_api.calls_named(name), [], name)
        # Category-level metrics are always refreshed.
        self.assertEqual(len(self.client_api.calls_named("metrics")), 1)

    def test_force_refetches_fresh_nav_and_holdings(self):
        self.sync(categories=["multi-cap"])
        self.client_api.calls.clear()
        self.sync(categories=["multi-cap"], force=True)
        self.assertEqual(len(self.client_api.calls_named("nav")), 2)
        self.assertEqual(len(self.client_api.calls_named("holdings")), 2)
        self.assertEqual(MFHoldingSnapshot.objects.count(), 2)

    def test_holdings_refetched_only_after_refresh_window(self):
        self.sync(categories=["multi-cap"])
        window = timedelta(days=HOLDINGS_REFRESH_DAYS)
        just_inside = timezone.now() - window + timedelta(minutes=5)
        just_outside = timezone.now() - window - timedelta(minutes=5)
        MFScheme.objects.filter(amfi_code=FUND_A).update(holdings_synced_at=just_outside)
        MFScheme.objects.filter(amfi_code=FUND_B).update(holdings_synced_at=just_inside)
        self.client_api.calls.clear()
        self.sync(categories=["multi-cap"], steps=[STEP_HOLDINGS])
        self.assertEqual([call[1] for call in self.client_api.calls_named("holdings")], [FUND_A])

    def test_never_synced_holdings_are_fetched(self):
        self.sync(categories=["multi-cap"], steps=[])
        self.assertTrue(all(scheme.holdings_synced_at is None for scheme in MFScheme.objects.all()))
        self.sync(categories=["multi-cap"], steps=[STEP_HOLDINGS])
        self.assertEqual(len(self.client_api.calls_named("holdings")), 2)

    def test_category_without_peer_schemes_fetches_nothing(self):
        self.client_api.scheme_master[1]["schemes"] = []
        self.client_api.metrics_by_category = lambda slug: {"category_slug": slug, "schemes": [], "category_medians": {}}
        run = self.sync(categories=["liquid"])
        self.assertEqual(run.status, MFSyncRun.STATUS_SUCCESS)
        self.assertEqual(run.stats["fund_schemes"], 0)
        self.assertEqual(self.client_api.calls_named("nav"), [])

    def test_empty_nav_and_holdings_payloads_store_nothing_but_mark_checked(self):
        self.client_api.nav_history = lambda code: {"scheme_code": code, "dates": None, "nav": None}
        self.client_api.holdings = lambda code, as_of=None, change=True: {"amfi_code": code, "as_of": None, "holdings": []}
        run = self.sync(categories=["multi-cap"], steps=[STEP_NAV, STEP_HOLDINGS])
        self.assertEqual(run.status, MFSyncRun.STATUS_SUCCESS)
        fund = MFScheme.objects.get(amfi_code=FUND_A)
        self.assertIsNone(fund.latest_nav)
        self.assertIsNone(fund.return_1y)
        self.assertIsNotNone(fund.nav_synced_at)
        self.assertIsNotNone(fund.holdings_synced_at)
        self.assertFalse(MFNavMonthly.objects.exists())
        self.assertFalse(MFHoldingSnapshot.objects.exists())

    def test_null_metric_values_are_stored_as_null(self):
        self.sync(categories=["multi-cap"], steps=[])
        metric = MFSchemeMetric.objects.get(scheme__amfi_code=FUND_A, horizon="3Y")
        self.assertIsNone(metric.information_ratio)  # fake payload sends None
        self.assertIsNotNone(metric.trailing_cagr)

    def test_missing_holdings_are_remembered_not_errors(self):
        self.client_api.missing_holdings.add(FUND_B)
        run = self.sync(categories=["multi-cap"])
        self.assertEqual(run.status, MFSyncRun.STATUS_SUCCESS)
        self.assertEqual(run.stats.get("holdings_not_found"), 1)
        self.assertIsNotNone(MFScheme.objects.get(amfi_code=FUND_B).holdings_synced_at)
        self.assertFalse(MFHoldingSnapshot.objects.filter(scheme__amfi_code=FUND_B).exists())

    def test_fetch_failures_make_the_run_partial(self):
        self.client_api.fail_codes.add(FUND_B)
        run = self.sync(categories=["multi-cap"])
        self.assertEqual(run.status, MFSyncRun.STATUS_PARTIAL)
        self.assertTrue(any(FUND_B in error for error in run.errors))
        self.assertIsNone(MFScheme.objects.get(amfi_code=FUND_B).return_1y)
        self.assertIsNotNone(MFScheme.objects.get(amfi_code=FUND_A).return_1y)

    def test_unknown_category_slug_makes_run_partial(self):
        run = self.sync(categories=["no-such-category"])
        self.assertEqual(run.status, MFSyncRun.STATUS_PARTIAL)
        self.assertIn("no-such-category", run.errors[0])

    def test_schemes_dropped_from_master_are_retired(self):
        self.sync(categories=["multi-cap"], steps=[])
        self.client_api.scheme_master[0]["schemes"] = self.client_api.scheme_master[0]["schemes"][:1]
        self.sync(categories=["multi-cap"], steps=[])
        self.assertFalse(MFScheme.objects.get(amfi_code=FUND_B).is_active)
        self.assertTrue(MFScheme.objects.get(amfi_code=FUND_A).is_active)

    def test_failure_before_fund_steps_marks_run_failed(self):
        with mock.patch.object(self.client_api, "categories", side_effect=RuntimeError("offline")):
            with self.assertRaises(RuntimeError):
                self.sync()
        run = MFSyncRun.objects.get()
        self.assertEqual(run.status, MFSyncRun.STATUS_FAILED)
        self.assertIsNotNone(run.finished_at)

    def test_running_rows_older_than_stale_window_are_closed(self):
        stale = MFSyncRun.objects.create(status=MFSyncRun.STATUS_RUNNING)
        MFSyncRun.objects.filter(pk=stale.pk).update(
            started_at=timezone.now() - timedelta(hours=STALE_RUN_HOURS, minutes=5)
        )
        recent = MFSyncRun.objects.create(status=MFSyncRun.STATUS_RUNNING)
        MFSyncRun.objects.filter(pk=recent.pk).update(
            started_at=timezone.now() - timedelta(hours=STALE_RUN_HOURS, minutes=-5)
        )
        self.sync(categories=["multi-cap"], steps=[])
        stale.refresh_from_db()
        recent.refresh_from_db()
        self.assertEqual(stale.status, MFSyncRun.STATUS_FAILED)
        self.assertEqual(recent.status, MFSyncRun.STATUS_RUNNING)


class CapClassificationSyncTests(TestCase):
    def test_index_proxy_used_when_amfi_list_unavailable(self):
        api = FakeCharlieAlpha()
        api.scheme_master.append({
            "category": "Index Fund - Broad Market",
            "category_slug": "index-broad-market",
            "asset_class": "Other",
            "total_schemes": 1,
            "schemes": [{"code": "300001", "name": "Delta Nifty 100 Index Fund - Regular Plan - Growth",
                         "amc": "Delta MF", "plan": "Regular", "option": "Growth"}],
        })
        with mock.patch.object(cap_list, "refresh_amfi_classification", return_value={"status": "unavailable"}):
            MFIntelligenceSync(client=api, workers=1).run(categories=["multi-cap"], steps=["cap_list"])
        self.assertEqual([call[1] for call in api.calls_named("holdings")], ["300001"])
        rows = MFStockClassification.objects.filter(source=MFStockClassification.SOURCE_INDEX_PROXY)
        self.assertEqual(set(rows.values_list("cap_bucket", flat=True)), {"Large Cap"})
        self.assertIn("INE040A01034", set(rows.values_list("isin", flat=True)))
