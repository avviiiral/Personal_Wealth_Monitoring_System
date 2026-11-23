"""Tests for the in-process masterdata pipeline scheduler (timing logic only)."""
from datetime import datetime, timedelta
from unittest import mock

from django.test import SimpleTestCase, TestCase

from mutual_funds.services.mf_pipeline import scheduler
from mutual_funds.services.mf_pipeline.scheduler import IST


def ist(*args):
    return datetime(*args, tzinfo=IST)


def slot_strings(job_name, start, end):
    spec = scheduler.JOB_SPECS[job_name]
    return [slot.strftime("%a %d %H:%M") for slot in scheduler.slots_between(spec, start, end)]


class SlotTests(SimpleTestCase):
    def test_daily_nav_runs_mon_to_sat_with_next_day_retries(self):
        # Sat 21-Nov-2026 00:00 -> Tue 24-Nov-2026 23:59 IST
        slots = slot_strings("mf_sync_nav", ist(2026, 11, 21), ist(2026, 11, 24, 23, 59))
        self.assertEqual(slots, [
            "Sat 21 09:30", "Sat 21 11:00", "Sat 21 23:30",  # Sat retries cover Fri's run
            "Sun 22 09:30", "Sun 22 11:00",                  # retries for Sat, no Sunday run
            "Mon 23 23:30",                                  # nothing to retry from Sunday
            "Tue 24 09:30", "Tue 24 11:00", "Tue 24 23:30",
        ])

    def test_calc_runs_daily_after_nav_load(self):
        slots = slot_strings("mf_calc_ratios", ist(2026, 11, 23), ist(2026, 11, 24, 23, 59))
        self.assertEqual(slots, ["Mon 23 00:30", "Tue 24 00:30"])

    def test_benchmark_trading_days_with_next_morning_retry(self):
        slots = slot_strings("mf_sync_benchmark", ist(2026, 11, 27), ist(2026, 11, 30, 23, 59))
        self.assertEqual(slots, ["Fri 27 09:00", "Fri 27 20:00", "Sat 28 09:00", "Mon 30 20:00"])

    def test_risk_parameters_monthly_on_10th_then_daily_until_20th(self):
        slots = slot_strings("mf_sync_risk", ist(2026, 12, 1), ist(2026, 12, 31, 23, 59))
        self.assertEqual(slots[0], "Thu 10 06:00")
        self.assertEqual(slots[-1], "Sun 20 06:00")
        self.assertEqual(len(slots), 11)

    def test_aum_same_window_as_risk(self):
        slots = slot_strings("mf_sync_aum", ist(2026, 12, 1), ist(2026, 12, 31, 23, 59))
        self.assertEqual((slots[0], slots[-1], len(slots)), ("Thu 10 06:00", "Sun 20 06:00", 11))

    def test_holdings_from_11th_through_debt_mid_month_window(self):
        slots = slot_strings("mf_sync_holdings", ist(2026, 12, 1), ist(2026, 12, 31, 23, 59))
        self.assertEqual(slots[0], "Fri 11 05:00")
        self.assertEqual(slots[-1], "Sun 20 05:00")

    def test_weekly_jobs(self):
        week = (ist(2026, 11, 23), ist(2026, 11, 29, 23, 59))
        self.assertEqual(slot_strings("mf_sync_ter", *week), ["Mon 23 06:00"])
        self.assertEqual(slot_strings("mf_nav_gap_check", *week), ["Sun 29 03:00"])
        # T-bill: Thursday, then daily retries (the job no-ops once loaded).
        self.assertEqual(len(slot_strings("mf_sync_risk_free", *week)), 7)

    def test_backfill_has_no_recurring_slot(self):
        self.assertEqual(slot_strings("mf_backfill_nav", ist(2026, 11, 1), ist(2026, 12, 31)), [])

    def test_slot_window_is_exclusive_start_inclusive_end(self):
        at = ist(2026, 11, 24, 0, 30)
        self.assertEqual(slot_strings("mf_calc_ratios", at, at + timedelta(minutes=1)), [])
        self.assertEqual(slot_strings("mf_calc_ratios", at - timedelta(minutes=1), at), ["Tue 24 00:30"])


class StartupTests(SimpleTestCase):
    def test_every_job_runs_on_startup_in_dependency_order(self):
        names = scheduler.startup_plan(now=ist(2026, 11, 23, 18, 0), last_success=lambda name: None)
        self.assertEqual(names[0], "mf_sync_nav")
        self.assertEqual(names[-1], "mf_calc_ratios")
        self.assertEqual(set(names), set(scheduler.JOB_SPECS))

    def test_recent_success_skips_job_on_restart(self):
        now = ist(2026, 11, 23, 18, 0)

        def last_success(name):
            return now - timedelta(minutes=30) if name == "mf_sync_nav" else None

        self.assertNotIn("mf_sync_nav", scheduler.startup_plan(now=now, last_success=last_success))

    def test_backfill_runs_on_startup_only_until_it_has_succeeded_once(self):
        now = ist(2026, 11, 23, 18, 0)
        plan = scheduler.startup_plan(now=now, last_success=lambda n: now - timedelta(days=90) if n == "mf_backfill_nav" else None)
        self.assertNotIn("mf_backfill_nav", plan)

    def test_scheduler_disabled_by_setting(self):
        with mock.patch.dict("os.environ", {"PWMS_MF_PIPELINE_ENABLED": "0"}):
            self.assertFalse(scheduler.pipeline_enabled())
        with mock.patch.dict("os.environ", {}, clear=False):
            self.assertTrue(scheduler.pipeline_enabled())


class InterruptedRunTests(TestCase):
    def test_runs_left_running_by_previous_process_are_closed(self):
        from django.utils import timezone

        from mutual_funds.master_models import MFSourceRun

        orphan = MFSourceRun.objects.create(source="X", command="mf_nav_gap_check", status=MFSourceRun.Status.RUNNING)
        MFSourceRun.objects.filter(id=orphan.id).update(started_at=timezone.now() - timedelta(minutes=5))
        other = MFSourceRun.objects.create(source="X", command="sync_mf_ter", status=MFSourceRun.Status.RUNNING)
        MFSourceRun.objects.filter(id=other.id).update(started_at=timezone.now() - timedelta(minutes=5))

        scheduler.MFPipelineScheduler._close_interrupted_runs(timezone.now() - timedelta(minutes=1))

        orphan.refresh_from_db()
        other.refresh_from_db()
        self.assertEqual(orphan.status, MFSourceRun.Status.FAILED)
        self.assertIn("Interrupted", orphan.errors[0]["error"])
        self.assertEqual(other.status, MFSourceRun.Status.RUNNING)  # not a pipeline job
