"""is_current() rules and run steps of every nightly job, with a fixed clock.

25 Nov 2026 is a Wednesday; 28 Nov is a Saturday; 30 Nov is a Monday.
"""
from contextlib import nullcontext
from datetime import date, datetime
from decimal import Decimal
from unittest.mock import call, patch
from zoneinfo import ZoneInfo

from django.contrib.auth.models import User
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from config.scheduling.framework import DailySchedule, IntervalSchedule, JobSkipped
from config.scheduling.registry import build_jobs
from investments.models import Asset, AssetCategory, Transaction
from market_data.models import DataSource, MarketPrice
from users.models import FamilyGroup

IST = ZoneInfo("Asia/Kolkata")

# Arbitrary fixture values; only their identity matters in these tests.
FIXTURE_NAV = Decimal("10")
UNIVERSE_DISCOVERED = 10
PERFORMANCE_UPDATED = 7
BENCHMARK_ERROR = "benchmark provider unavailable"


def ist(year, month, day, hour=0, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=IST)


class RegistryTests(SimpleTestCase):
    def test_default_schedule_matches_the_table(self):
        jobs = {job.name: job for job in build_jobs()}
        self.assertEqual(list(jobs), [
            "market_prices", "mf_nav", "sip", "mf_intelligence", "watchlist",
            "security_master", "security_metrics", "mf_underlying", "corporate_actions",
        ])
        self.assertIsInstance(jobs["market_prices"].schedule, IntervalSchedule)
        self.assertEqual(jobs["market_prices"].schedule.minutes, 15)
        expected_times = {
            "mf_nav": "00:00", "mf_intelligence": "01:00", "security_master": "02:00",
            "security_metrics": "02:30", "mf_underlying": "03:00", "corporate_actions": "03:30",
        }
        for name, hhmm in expected_times.items():
            self.assertIsInstance(jobs[name].schedule, DailySchedule, name)
            self.assertEqual(jobs[name].schedule.hhmm, hhmm, name)
        self.assertEqual(jobs["sip"].after, "mf_nav")
        self.assertTrue(jobs["sip"].requires_parent_success)
        self.assertEqual(jobs["watchlist"].after, "mf_intelligence")
        self.assertTrue(all(job.enabled for job in jobs.values()))


class NavPublishDateTests(SimpleTestCase):
    def setUp(self):
        from mutual_funds.jobs import expected_publish_date

        self.expected = expected_publish_date

    def test_before_cutoff_expects_previous_weekday(self):
        self.assertEqual(self.expected(ist(2026, 11, 25, 10, 0), "23:00"), date(2026, 11, 24))

    def test_after_cutoff_expects_today(self):
        self.assertEqual(self.expected(ist(2026, 11, 25, 23, 30), "23:00"), date(2026, 11, 25))

    def test_weekend_expects_friday(self):
        self.assertEqual(self.expected(ist(2026, 11, 28, 10, 0), "23:00"), date(2026, 11, 27))
        self.assertEqual(self.expected(ist(2026, 11, 28, 23, 30), "23:00"), date(2026, 11, 27))

    def test_exact_cutoff_boundary(self):
        # 22:59 is before the cutoff; 23:00 itself counts as "past" it.
        self.assertEqual(self.expected(ist(2026, 11, 25, 22, 59), "23:00"), date(2026, 11, 24))
        self.assertEqual(self.expected(ist(2026, 11, 25, 23, 0), "23:00"), date(2026, 11, 25))

    def test_midnight_job_expects_last_weekday(self):
        self.assertEqual(self.expected(ist(2026, 11, 30, 0, 0), "23:00"), date(2026, 11, 27))
        self.assertEqual(self.expected(ist(2026, 11, 26, 0, 0), "23:00"), date(2026, 11, 25))


class FundFixtureMixin:
    def make_family(self):
        self.user = User.objects.create_user(username="nav_owner", password="pw")
        self.family = FamilyGroup.objects.create(name="NAV Family")

    def fund(self, name, isin, *, category=AssetCategory.MUTUAL_FUND, sub_class="Mutual Fund", asset_class="Equity"):
        asset = Asset.objects.create(owner=self.user, family=self.family, name=name, category=category, isin=isin,
                                     currency="INR", is_active=True)
        Transaction.objects.create(
            owner=self.user, family=self.family, family_name="M", portfolio="P", asset_class=asset_class,
            sub_class=sub_class, asset_name=name, underlying="", asset=asset, transaction_type="BUY",
            transaction_date=date(2026, 1, 5), quantity=Decimal("1"), price_per_unit=FIXTURE_NAV,
            amount=FIXTURE_NAV, fees=Decimal("0"),
        )
        return asset

    def nav(self, asset, day):
        MarketPrice.objects.create(asset=asset, date=day, source=DataSource.AMFI, close_price=FIXTURE_NAV,
                                   adjusted_close=FIXTURE_NAV)


class MutualFundNavJobTests(FundFixtureMixin, TestCase):
    def setUp(self):
        from mutual_funds.jobs import MutualFundNavJob

        self.make_family()
        self.job = MutualFundNavJob()
        self.equity = self.fund("Equity Fund Growth", "INF000E01011")
        self.debt = self.fund("BANDHAN Dynamic Bond Fund-Direct Plan-Growth", "INF194K01Y29",
                              category=AssetCategory.BOND, asset_class="Debt")
        self.no_isin = self.fund("Old Fund", None)

    def test_current_when_every_isin_fund_has_the_expected_nav(self):
        self.nav(self.equity, date(2026, 11, 24))
        self.nav(self.debt, date(2026, 11, 24))
        current, reason = self.job.is_current(ist(2026, 11, 25, 9, 0), last_success=None, at_startup=True)
        self.assertTrue(current, reason)

    def test_not_current_when_one_fund_is_behind(self):
        self.nav(self.equity, date(2026, 11, 24))
        self.nav(self.debt, date(2026, 11, 21))
        current, reason = self.job.is_current(ist(2026, 11, 25, 9, 0), last_success=None, at_startup=True)
        self.assertFalse(current)
        self.assertIn("1", reason)

    def test_holiday_counts_as_current_after_a_successful_run_past_the_cutoff(self):
        # 24 Nov was a holiday: AMFI published nothing new, so funds stay at 23 Nov.
        self.nav(self.equity, date(2026, 11, 23))
        self.nav(self.debt, date(2026, 11, 23))
        now = ist(2026, 11, 25, 9, 0)
        self.assertFalse(self.job.is_current(now, last_success=ist(2026, 11, 24, 22, 0), at_startup=True)[0])
        self.assertTrue(self.job.is_current(now, last_success=ist(2026, 11, 25, 0, 0), at_startup=True)[0])

    def test_success_exactly_at_the_cutoff_counts_for_a_holiday(self):
        self.nav(self.equity, date(2026, 11, 23))
        self.nav(self.debt, date(2026, 11, 23))
        now = ist(2026, 11, 25, 9, 0)
        self.assertTrue(self.job.is_current(now, last_success=ist(2026, 11, 24, 23, 0), at_startup=True)[0])
        self.assertFalse(self.job.is_current(now, last_success=ist(2026, 11, 24, 22, 59), at_startup=True)[0])

    def test_run_follows_the_documented_steps_in_order(self):
        calls = []
        with (
            patch("mutual_funds.services.investment_amfi.InvestmentAMFIService.refresh_for_investments_with_history",
                  side_effect=lambda: calls.append("amfi") or {"requested_isins": 2}),
            patch("market_data.services.mutual_fund_nav_service.MutualFundNAVService.clear_cache",
                  side_effect=lambda: calls.append("clear")),
            patch("market_data.services.market_data_manager.MarketDataManager._fetch_amfi_nav",
                  side_effect=lambda asset: calls.append(("nav", asset.id)) or {"success": True}),
            patch("mutual_funds.services.amfi.AMFIService.sync_owned_navs_from_master",
                  side_effect=lambda user: calls.append(("sync", user.id)) or {}),
            patch("market_data.services.market_data_manager.BondPriceService.get_latest_price") as bond,
        ):
            counts = self.job.run(ist(2026, 11, 26, 0, 0))
        bond.assert_not_called()
        self.assertEqual(calls[0], "clear")
        self.assertEqual(calls[1], "amfi")
        self.assertEqual(sorted(call for call in calls if isinstance(call, tuple) and call[0] == "nav"),
                         sorted([("nav", self.equity.id), ("nav", self.debt.id)]))
        self.assertEqual(calls[-1], ("sync", self.user.id))
        self.assertEqual(counts["funds"], 2)
        self.assertEqual(counts["no_isin"], 1)


class EmptyPortfolioNavJobTests(TestCase):
    def test_no_funds_is_current_and_run_is_a_no_op(self):
        from mutual_funds.jobs import MutualFundNavJob

        job = MutualFundNavJob()
        current, reason = job.is_current(ist(2026, 11, 25, 9, 0), last_success=None, at_startup=True)
        self.assertTrue(current, reason)
        with (
            patch("mutual_funds.services.investment_amfi.InvestmentAMFIService.refresh_for_investments_with_history",
                  return_value={"requested_isins": 0}),
            patch("market_data.services.mutual_fund_nav_service.MutualFundNAVService.clear_cache"),
            patch("market_data.services.market_data_manager.MarketDataManager._fetch_amfi_nav") as fetch,
        ):
            counts = job.run(ist(2026, 11, 26, 0, 0))
        fetch.assert_not_called()
        self.assertEqual(counts["funds"], 0)


class SipJobTests(FundFixtureMixin, TestCase):
    def test_no_active_users_runs_nothing(self):
        from mutual_funds.jobs import SipJob

        User.objects.create_user(username="inactive_only", password="pw", is_active=False)
        with patch("mutual_funds.jobs.call_command") as command:
            counts = SipJob().run(ist(2026, 11, 26, 0, 5))
        command.assert_not_called()
        self.assertEqual(counts["users"], 0)


    def test_runs_sync_then_execute_for_each_active_user(self):
        from mutual_funds.jobs import SipJob

        self.make_family()
        User.objects.create_user(username="inactive", password="pw", is_active=False)
        with patch("mutual_funds.jobs.call_command") as command:
            counts = SipJob().run(ist(2026, 11, 26, 0, 5))
        self.assertEqual(command.call_args_list, [
            call("sync_sip_installments", user_id=self.user.id),
            call("execute_sips", user_id=self.user.id),
        ])
        self.assertEqual(counts["users"], 1)

    def test_current_after_success_since_midnight(self):
        from mutual_funds.jobs import SipJob

        job = SipJob()
        self.assertTrue(job.is_current(ist(2026, 11, 26, 9, 0), last_success=ist(2026, 11, 26, 0, 3), at_startup=True)[0])
        self.assertFalse(job.is_current(ist(2026, 11, 26, 9, 0), last_success=ist(2026, 11, 25, 0, 3), at_startup=True)[0])


class MFIntelligenceJobTests(TestCase):
    def setUp(self):
        from mf_intelligence.jobs import MFIntelligenceJob

        self.job = MFIntelligenceJob()

    def _run(self, *, status, finished, scope=None):
        from mf_intelligence.models import MFSyncRun

        run = MFSyncRun.objects.create(status=status, trigger="scheduled",
                                       scope=scope or {"categories": "all", "plans": ["Regular"], "steps": [
                                           "nav", "holdings", "aum", "benchmarks", "cap_list"], "limit": None})
        MFSyncRun.objects.filter(pk=run.pk).update(finished_at=finished)
        return run

    def test_current_after_a_successful_or_partial_run_since_01_00(self):
        from mf_intelligence.models import MFSyncRun

        now = timezone.make_aware(datetime(2026, 11, 25, 9, 0), IST)
        self._run(status=MFSyncRun.STATUS_PARTIAL, finished=ist(2026, 11, 25, 1, 40))
        self.assertTrue(self.job.is_current(now, last_success=None, at_startup=True)[0])

    def test_failed_or_older_runs_are_not_current(self):
        from mf_intelligence.models import MFSyncRun

        now = ist(2026, 11, 25, 9, 0)
        self._run(status=MFSyncRun.STATUS_FAILED, finished=ist(2026, 11, 25, 1, 40))
        self._run(status=MFSyncRun.STATUS_SUCCESS, finished=ist(2026, 11, 24, 23, 0))
        self.assertFalse(self.job.is_current(now, last_success=None, at_startup=True)[0])

    def test_a_single_category_ui_run_does_not_count(self):
        from mf_intelligence.models import MFSyncRun

        self._run(status=MFSyncRun.STATUS_SUCCESS, finished=ist(2026, 11, 25, 2, 0),
                  scope={"categories": ["large-cap"], "plans": ["Regular"], "steps": ["nav"], "limit": None})
        self.assertFalse(self.job.is_current(ist(2026, 11, 25, 9, 0), last_success=None, at_startup=True)[0])

    def test_does_not_start_while_a_ui_sync_holds_the_lock(self):
        from mf_intelligence.services.sync import SYNC_LOCK

        self.assertTrue(SYNC_LOCK.acquire(blocking=False))
        try:
            with patch("mf_intelligence.services.sync.MFIntelligenceSync.run") as run:
                with self.assertRaises(JobSkipped):
                    self.job.run(ist(2026, 11, 25, 1, 0))
            run.assert_not_called()
        finally:
            SYNC_LOCK.release()

    def test_does_not_start_while_a_recent_run_row_is_still_running(self):
        from mf_intelligence.models import MFSyncRun

        MFSyncRun.objects.create(status=MFSyncRun.STATUS_RUNNING, trigger="manual")
        with patch("mf_intelligence.services.sync.MFIntelligenceSync.run") as run:
            with self.assertRaises(JobSkipped):
                self.job.run(timezone.localtime())
        run.assert_not_called()

    def test_run_uses_the_scheduled_scope_and_releases_the_lock(self):
        from mf_intelligence.models import MFSyncRun
        from mf_intelligence.services.sync import SYNC_LOCK

        def fake_run(self_, **kwargs):
            return MFSyncRun(status=MFSyncRun.STATUS_SUCCESS, stats={"requests": 5})

        with patch("mf_intelligence.services.sync.MFIntelligenceSync.run", autospec=True, side_effect=fake_run) as run:
            counts = self.job.run(ist(2026, 11, 25, 1, 0))
        kwargs = run.call_args.kwargs
        self.assertEqual(kwargs["trigger"], "scheduled")
        self.assertIsNone(kwargs["categories"])
        self.assertEqual(list(kwargs["plans"]), ["Regular"])
        self.assertFalse(SYNC_LOCK.locked())
        self.assertEqual(counts["status"], "success")


class WatchListJobTests(SimpleTestCase):
    def test_runs_every_existing_refresh_step_and_reports_failures(self):
        from watchlist.jobs import WatchListJob

        with (
            patch("watchlist.services.benchmark.BenchmarkPerformanceService.ensure_benchmark_master_history",
                  side_effect=RuntimeError(BENCHMARK_ERROR)) as benchmark,
            patch("watchlist.services.background_refresh.BackgroundWatchListRefreshService.refresh",
                  return_value={"mutual_funds": {}}) as background,
            patch("watchlist.services.universe.AMFIUniverseService.refresh",
                  return_value={"discovered": UNIVERSE_DISCOVERED}) as universe,
            patch("watchlist.services.performance.AMFIPerformanceService.refresh",
                  return_value={"metrics_updated": PERFORMANCE_UPDATED}) as performance,
        ):
            counts = WatchListJob().run(ist(2026, 11, 25, 1, 30))
        for step in (benchmark, background, universe, performance):
            step.assert_called_once()
        self.assertEqual(counts["failed_steps"], 1)
        self.assertEqual(counts["universe_discovered"], UNIVERSE_DISCOVERED)
        self.assertEqual(counts["performance_updated"], PERFORMANCE_UPDATED)

    def test_fails_when_every_step_fails(self):
        from watchlist.jobs import WatchListJob

        boom = RuntimeError("offline")
        with (
            patch("watchlist.services.benchmark.BenchmarkPerformanceService.ensure_benchmark_master_history", side_effect=boom),
            patch("watchlist.services.background_refresh.BackgroundWatchListRefreshService.refresh", side_effect=boom),
            patch("watchlist.services.universe.AMFIUniverseService.refresh", side_effect=boom),
            patch("watchlist.services.performance.AMFIPerformanceService.refresh", side_effect=boom),
        ):
            with self.assertRaises(RuntimeError):
                WatchListJob().run(ist(2026, 11, 25, 1, 30))

    def test_current_after_success_since_01_00(self):
        from watchlist.jobs import WatchListJob

        job = WatchListJob()
        self.assertTrue(job.is_current(ist(2026, 11, 25, 9, 0), last_success=ist(2026, 11, 25, 1, 50), at_startup=True)[0])
        self.assertFalse(job.is_current(ist(2026, 11, 25, 9, 0), last_success=ist(2026, 11, 25, 0, 50), at_startup=True)[0])


class MaxAgeJobTests(SimpleTestCase):
    def test_security_master_metrics_and_underlying_use_the_24_hour_startup_rule(self):
        from market_data.jobs import SecurityMasterJob, SecurityMetricsJob
        from mutual_funds.jobs import MutualFundUnderlyingJob

        for job in (SecurityMasterJob(), SecurityMetricsJob(), MutualFundUnderlyingJob()):
            # Restarted 10 times today: last success 5 h ago -> not re-run.
            self.assertTrue(job.is_current(ist(2026, 11, 25, 12, 0), last_success=ist(2026, 11, 25, 7, 0),
                                           at_startup=True)[0], job.name)
            self.assertFalse(job.is_current(ist(2026, 11, 25, 12, 0), last_success=ist(2026, 11, 24, 11, 0),
                                            at_startup=True)[0], job.name)

    def test_underlying_job_calls_the_existing_command(self):
        from mutual_funds.jobs import MutualFundUnderlyingJob

        with patch("mutual_funds.jobs.call_command") as command:
            MutualFundUnderlyingJob().run(ist(2026, 11, 25, 3, 0))
        command.assert_called_once()
        self.assertEqual(command.call_args.args, ("fetch_mf_underlying",))


class CorporateActionsJobTests(SimpleTestCase):
    def test_current_only_when_todays_scan_succeeded(self):
        from corporate_actions.jobs import CorporateActionsJob

        job = CorporateActionsJob()
        self.assertTrue(job.is_current(ist(2026, 11, 25, 9, 0), last_success=ist(2026, 11, 25, 3, 30), at_startup=True)[0])
        self.assertFalse(job.is_current(ist(2026, 11, 25, 9, 0), last_success=ist(2026, 11, 24, 3, 30), at_startup=True)[0])
        self.assertFalse(job.is_current(ist(2026, 11, 25, 9, 0), last_success=None, at_startup=True)[0])

    def test_run_scans_nse_and_raises_on_failure(self):
        from corporate_actions.jobs import CorporateActionsJob

        with patch("corporate_actions.jobs.call_command") as command:
            CorporateActionsJob().run(ist(2026, 11, 25, 3, 30))
        command.assert_called_once()
        self.assertEqual(command.call_args.args, ("monitor_corporate_actions",))
        self.assertEqual(command.call_args.kwargs["source"], "nse")

        with patch("corporate_actions.jobs.call_command", side_effect=RuntimeError("nse blocked")):
            with self.assertRaises(RuntimeError):
                CorporateActionsJob().run(ist(2026, 11, 25, 3, 30))


class RunScheduledJobCommandTests(SimpleTestCase):
    def test_runs_one_job_even_when_current(self):
        from io import StringIO
        import tempfile
        from pathlib import Path

        from django.core.management import call_command

        with tempfile.TemporaryDirectory() as directory, \
                self.settings(PWMS_DATA_DIR=directory), \
                patch("corporate_actions.jobs.CorporateActionsJob.run", return_value={"scanned": 3}) as run, \
                patch("config.scheduling.framework.JobRunner._db_lock", return_value=nullcontext(True)), \
                patch("django.db.close_old_connections"):
            out = StringIO()
            call_command("run_scheduled_job", "corporate_actions", stdout=out)
            run.assert_called_once()
            self.assertIn("status=success", out.getvalue())
            self.assertTrue((Path(directory) / "scheduler_state.json").exists())

    def test_unknown_job_lists_the_valid_names(self):
        from io import StringIO

        from django.core.management import CommandError, call_command

        with self.assertRaises(CommandError) as error:
            call_command("run_scheduled_job", "nope", stdout=StringIO())
        self.assertIn("mf_nav", str(error.exception))

    def test_check_only_reports_without_running(self):
        from io import StringIO

        from django.core.management import call_command

        with patch("corporate_actions.jobs.CorporateActionsJob.run") as run, \
                patch("corporate_actions.jobs.CorporateActionsJob.is_current", return_value=(True, "ran today")):
            out = StringIO()
            call_command("run_scheduled_job", "corporate_actions", check=True, stdout=out)
        run.assert_not_called()
        self.assertIn("current", out.getvalue())
