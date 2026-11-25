"""Tests for the background-job framework (config/scheduling).

Every test drives the framework with an explicit clock value, so nothing here
depends on the real time of day or sleeps for a scheduled slot.
"""
import json
import tempfile
import threading
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.test import SimpleTestCase

from config.scheduling import clock
from config.scheduling.deadline import DeadlineExceeded, DeadlineExecutor
from config.scheduling.files import atomic_write_text
from config.scheduling.framework import (
    DailySchedule,
    IntervalSchedule,
    JobRunner,
    NightlyWorker,
    ScheduledJob,
    max_age_is_current,
    slot_is_current,
)
from config.scheduling.state import JobStateStore

IST = ZoneInfo("Asia/Kolkata")


def ist(year, month, day, hour=0, minute=0, second=0):
    return datetime(year, month, day, hour, minute, second, tzinfo=IST)


class DailySlotTests(SimpleTestCase):
    def test_latest_slot_is_today_once_the_time_has_passed(self):
        self.assertEqual(clock.latest_daily_slot(ist(2026, 11, 25, 2, 5), "02:00"), ist(2026, 11, 25, 2, 0))

    def test_latest_slot_is_yesterday_before_the_time(self):
        self.assertEqual(clock.latest_daily_slot(ist(2026, 11, 25, 1, 59), "02:00"), ist(2026, 11, 24, 2, 0))

    def test_slot_exactly_now_counts_as_latest(self):
        self.assertEqual(clock.latest_daily_slot(ist(2026, 11, 25, 0, 0), "00:00"), ist(2026, 11, 25, 0, 0))

    def test_next_slot_is_strictly_after_now(self):
        self.assertEqual(clock.next_daily_slot(ist(2026, 11, 25, 0, 0), "00:00"), ist(2026, 11, 26, 0, 0))
        self.assertEqual(clock.next_daily_slot(ist(2026, 11, 25, 1, 0), "03:30"), ist(2026, 11, 25, 3, 30))

    def test_invalid_time_is_rejected(self):
        with self.assertRaises(ValueError):
            clock.parse_hhmm("25:00")


class MarketCalendarTests(SimpleTestCase):
    def setUp(self):
        self.calendar = clock.MarketCalendar(
            open_time="09:15", close_time="15:30", buffer_minutes=15, holidays=["2026-11-24"],
        )

    def test_market_hours_include_the_close_buffer(self):
        self.assertTrue(self.calendar.in_market_hours(ist(2026, 11, 25, 9, 15)))
        self.assertTrue(self.calendar.in_market_hours(ist(2026, 11, 25, 15, 44)))
        self.assertFalse(self.calendar.in_market_hours(ist(2026, 11, 25, 15, 45)))
        self.assertFalse(self.calendar.in_market_hours(ist(2026, 11, 25, 9, 14)))

    def test_weekend_and_configured_holiday_are_not_sessions(self):
        self.assertFalse(self.calendar.in_market_hours(ist(2026, 11, 28, 11, 0)))  # Saturday
        self.assertFalse(self.calendar.in_market_hours(ist(2026, 11, 24, 11, 0)))  # holiday

    def test_last_session_end_after_close_is_today(self):
        self.assertEqual(self.calendar.last_session_end(ist(2026, 11, 25, 20, 0)), ist(2026, 11, 25, 15, 45))

    def test_last_session_end_before_open_is_previous_session(self):
        # 24 Nov is a configured holiday, so the previous session is 23 Nov.
        self.assertEqual(self.calendar.last_session_end(ist(2026, 11, 25, 8, 0)), ist(2026, 11, 23, 15, 45))

    def test_last_session_end_on_weekend_is_friday(self):
        self.assertEqual(self.calendar.last_session_end(ist(2026, 11, 29, 12, 0)), ist(2026, 11, 27, 15, 45))

    def test_last_weekday_helpers(self):
        self.assertEqual(clock.last_weekday_on_or_before(date(2026, 11, 29)), date(2026, 11, 27))
        self.assertEqual(clock.last_weekday_before(date(2026, 11, 30)), date(2026, 11, 27))
        self.assertEqual(clock.last_weekday_before(date(2026, 11, 26)), date(2026, 11, 25))


class AtomicWriteTests(SimpleTestCase):
    def test_write_replaces_target(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "state.json"
            atomic_write_text(target, "first")
            atomic_write_text(target, "second")
            self.assertEqual(target.read_text(encoding="utf-8"), "second")
            self.assertEqual(sorted(p.name for p in Path(directory).iterdir()), ["state.json"])

    def test_locked_target_is_retried_then_succeeds(self):
        # WinError 32 / WinError 5 surface as PermissionError from os.replace.
        import os

        real_replace = os.replace
        calls = {"count": 0}

        def flaky_replace(src, dst):
            calls["count"] += 1
            if calls["count"] < 3:
                raise PermissionError(32, "The process cannot access the file")
            return real_replace(src, dst)

        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "state.json"
            with patch("config.scheduling.files.os.replace", side_effect=flaky_replace):
                atomic_write_text(target, "value", retries=5, retry_delay=0)
            self.assertEqual(target.read_text(encoding="utf-8"), "value")
            self.assertEqual(calls["count"], 3)

    def test_permanently_locked_target_raises_and_leaves_no_temp_file(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "state.json"
            target.write_text("original", encoding="utf-8")
            with patch("config.scheduling.files.os.replace", side_effect=PermissionError(5, "Access is denied")):
                with self.assertRaises(PermissionError):
                    atomic_write_text(target, "new", retries=2, retry_delay=0)
            self.assertEqual(target.read_text(encoding="utf-8"), "original")
            self.assertEqual(sorted(p.name for p in Path(directory).iterdir()), ["state.json"])


class JobStateStoreTests(SimpleTestCase):
    def _record(self, store, job, when):
        store.record(job, status="success", started_at=when, finished_at=when + timedelta(seconds=5), reason="manual")

    def test_two_processes_do_not_overwrite_each_others_jobs(self):
        # The server keeps its state in memory; `run_scheduled_job` is another
        # process writing the same file. Neither may erase the other's jobs.
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "s.json"
            server, command = JobStateStore(path), JobStateStore(path)
            self._record(server, "market_prices", ist(2026, 11, 25, 16, 0))   # server has loaded its copy
            self._record(command, "mf_nav", ist(2026, 11, 25, 16, 54))        # another process writes mf_nav
            self._record(server, "security_master", ist(2026, 11, 25, 17, 9))  # server saves again
            fresh = JobStateStore(path)
            for job in ("market_prices", "mf_nav", "security_master"):
                self.assertIsNotNone(fresh.last_success(job), job)
            self.assertIsNotNone(server.last_success("mf_nav"))  # the server sees it too

    def test_other_process_write_is_visible_without_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "s.json"
            server, command = JobStateStore(path), JobStateStore(path)
            self.assertIsNone(server.last_success("mf_nav"))
            self._record(command, "mf_nav", ist(2026, 11, 25, 16, 54))
            self.assertEqual(server.last_success("mf_nav"), ist(2026, 11, 25, 16, 54))

    def test_unsaved_value_survives_a_locked_file_and_a_foreign_write(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "s.json"
            server, command = JobStateStore(path), JobStateStore(path)
            with patch("config.scheduling.state.atomic_write_text", side_effect=PermissionError(13, "locked")):
                self._record(server, "market_prices", ist(2026, 11, 25, 16, 0))  # kept in memory only
            self._record(command, "mf_nav", ist(2026, 11, 25, 16, 54))
            self._record(server, "sip", ist(2026, 11, 25, 17, 0))                # next save writes everything
            fresh = JobStateStore(path)
            for job in ("market_prices", "mf_nav", "sip"):
                self.assertIsNotNone(fresh.last_success(job), job)

    def test_success_round_trips_through_the_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "scheduler_state.json"
            store = JobStateStore(path)
            started = ist(2026, 11, 25, 0, 0)
            store.record("mf_nav", status="success", started_at=started, finished_at=started + timedelta(seconds=5),
                         reason="slot", counts={"funds": 3}, slot=started)

            reloaded = JobStateStore(path)
            self.assertEqual(reloaded.last_success("mf_nav"), started)
            self.assertEqual(reloaded.get("mf_nav")["last_counts"], {"funds": 3})

    def test_failure_does_not_move_last_success(self):
        with tempfile.TemporaryDirectory() as directory:
            store = JobStateStore(Path(directory) / "s.json")
            first = ist(2026, 11, 24, 0, 0)
            store.record("mf_nav", status="success", started_at=first, finished_at=first, reason="slot")
            store.record("mf_nav", status="failed", started_at=first + timedelta(days=1),
                         finished_at=first + timedelta(days=1), reason="slot", error="boom")
            self.assertEqual(store.last_success("mf_nav"), first)
            self.assertEqual(store.get("mf_nav")["last_status"], "failed")

    def test_corrupt_file_is_treated_as_empty(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "s.json"
            path.write_text("{not json", encoding="utf-8")
            self.assertIsNone(JobStateStore(path).last_success("anything"))

    def test_locked_state_file_keeps_value_in_memory(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "s.json"
            store = JobStateStore(path)
            started = ist(2026, 11, 25, 1, 0)
            with patch("config.scheduling.state.atomic_write_text", side_effect=PermissionError(32, "locked")):
                store.record("watchlist", status="success", started_at=started, finished_at=started, reason="slot")
            self.assertEqual(store.last_success("watchlist"), started)
            # The next successful write persists the pending value too.
            store.record("other", status="success", started_at=started, finished_at=started, reason="slot")
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertIn("watchlist", data["jobs"])


class DeadlineExecutorTests(SimpleTestCase):
    def test_returns_the_function_result(self):
        executor = DeadlineExecutor("test")
        try:
            self.assertEqual(executor.run(lambda: 42, timeout=2), 42)
        finally:
            executor.close()

    def test_exception_is_re_raised(self):
        executor = DeadlineExecutor("test")
        try:
            with self.assertRaises(ZeroDivisionError):
                executor.run(lambda: 1 / 0, timeout=2)
        finally:
            executor.close()

    def test_hanging_call_costs_at_most_one_deadline_and_next_call_runs(self):
        release = threading.Event()
        executor = DeadlineExecutor("test")
        try:
            started = time.monotonic()
            with self.assertRaises(DeadlineExceeded):
                executor.run(release.wait, timeout=0.2)
            elapsed = time.monotonic() - started
            self.assertLess(elapsed, 1.0)
            # A fresh worker serves the next call even though the first hangs.
            self.assertEqual(executor.run(lambda: "next", timeout=2), "next")
            self.assertEqual(executor.abandoned_count(), 1)
        finally:
            release.set()
            executor.close()


class FakeJob(ScheduledJob):
    def __init__(self, name, schedule, *, current=False, fail=False, after=None, requires_parent_success=False,
                 runs=None):
        super().__init__(name=name, schedule=schedule, after=after,
                         requires_parent_success=requires_parent_success)
        self._current = current
        self._fail = fail
        self.runs = runs if runs is not None else []

    def is_current(self, now, *, last_success, at_startup):
        if callable(self._current):
            return self._current(now, last_success, at_startup)
        return bool(self._current), "fake"

    def run(self, now):
        self.runs.append((self.name, now))
        if self._fail:
            raise RuntimeError(f"{self.name} exploded")
        return {"items": 1}


class JobRunnerTests(SimpleTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = JobStateStore(Path(self.directory.name) / "state.json")
        self.runner = JobRunner(self.store, use_db_lock=False)

    def test_current_job_is_skipped_without_running(self):
        job = FakeJob("a", DailySchedule("00:00"), current=True)
        outcome = self.runner.execute(job, now=ist(2026, 11, 25, 9, 0), reason="startup")
        self.assertEqual(outcome.status, "current")
        self.assertEqual(job.runs, [])

    def test_exception_is_recorded_and_not_raised(self):
        job = FakeJob("a", DailySchedule("00:00"), fail=True)
        with self.assertLogs("config.scheduling.framework", level="INFO") as logs:
            outcome = self.runner.execute(job, now=ist(2026, 11, 25, 9, 0), reason="startup")
        self.assertEqual(outcome.status, "failed")
        self.assertIn("exploded", outcome.error)
        self.assertEqual(self.store.get("a")["last_status"], "failed")
        start_lines = [line for line in logs.output if "job a started" in line]
        end_lines = [line for line in logs.output if "job a finished" in line]
        self.assertEqual(len(start_lines), 1)
        self.assertEqual(len(end_lines), 1)

    def test_success_records_counts_and_duration_in_one_end_line(self):
        job = FakeJob("a", DailySchedule("00:00"))
        with self.assertLogs("config.scheduling.framework", level="INFO") as logs:
            self.runner.execute(job, now=ist(2026, 11, 25, 9, 0), reason="manual")
        end_line = next(line for line in logs.output if "job a finished" in line)
        self.assertIn("status=success", end_line)
        self.assertIn("duration=", end_line)
        self.assertIn("items=1", end_line)
        self.assertEqual(self.store.last_success("a"), ist(2026, 11, 25, 9, 0))

    def test_force_runs_a_current_job(self):
        job = FakeJob("a", DailySchedule("00:00"), current=True)
        outcome = self.runner.execute(job, now=ist(2026, 11, 25, 9, 0), reason="manual", force=True)
        self.assertEqual(outcome.status, "success")
        self.assertEqual(len(job.runs), 1)

    def test_attempts_per_slot_are_capped(self):
        job = FakeJob("a", DailySchedule("00:00"), fail=True)
        runner = JobRunner(self.store, use_db_lock=False, max_attempts_per_slot=2)
        now = ist(2026, 11, 25, 9, 0)
        runner.execute(job, now=now, reason="startup")
        runner.execute(job, now=now + timedelta(hours=1), reason="retry")
        outcome = runner.execute(job, now=now + timedelta(hours=2), reason="retry")
        self.assertEqual(outcome.status, "gave_up")
        self.assertEqual(len(job.runs), 2)
        # A new slot resets the counter.
        runner.execute(job, now=ist(2026, 11, 26, 0, 5), reason="slot")
        self.assertEqual(len(job.runs), 3)


class CurrencyRuleTests(SimpleTestCase):
    def test_slot_rule(self):
        now = ist(2026, 11, 25, 9, 0)
        self.assertFalse(slot_is_current(now, "00:00", None)[0])
        self.assertTrue(slot_is_current(now, "00:00", ist(2026, 11, 25, 0, 1))[0])
        self.assertFalse(slot_is_current(now, "00:00", ist(2026, 11, 24, 23, 59))[0])

    def test_max_age_rule_at_startup_uses_24_hours(self):
        now = ist(2026, 11, 25, 1, 0)
        # Ran 23 h ago at a startup catch-up: still current.
        self.assertTrue(max_age_is_current(now, "02:00", ist(2026, 11, 24, 2, 0), at_startup=True,
                                           max_age_hours=24, min_gap_hours=12)[0])
        # More than 24 h ago: due.
        self.assertFalse(max_age_is_current(ist(2026, 11, 25, 10, 0), "02:00", ist(2026, 11, 24, 2, 10),
                                            at_startup=True, max_age_hours=24, min_gap_hours=12)[0])

    def test_max_age_rule_at_slot_does_not_drift(self):
        # Yesterday's run started at 02:20 because earlier jobs ran long.
        # Today's 02:00 slot must still run, not wait until 02:20.
        self.assertFalse(max_age_is_current(ist(2026, 11, 25, 2, 0), "02:00", ist(2026, 11, 24, 2, 20),
                                            at_startup=False, max_age_hours=24, min_gap_hours=12)[0])

    def test_max_age_rule_at_slot_skips_a_recent_startup_run(self):
        # A startup catch-up at 23:00 makes the 02:00 slot redundant.
        self.assertTrue(max_age_is_current(ist(2026, 11, 25, 2, 0), "02:00", ist(2026, 11, 24, 23, 0),
                                           at_startup=False, max_age_hours=24, min_gap_hours=12)[0])


class NightlyWorkerTests(SimpleTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = JobStateStore(Path(self.directory.name) / "state.json")
        self.runner = JobRunner(self.store, use_db_lock=False)
        self.runs = []

    def _slot_job(self, name, hhmm, **kwargs):
        def current(now, last_success, at_startup):
            return slot_is_current(now, hhmm, last_success)

        return FakeJob(name, DailySchedule(hhmm), current=current, runs=self.runs, **kwargs)

    def _chained(self, name, parent, parent_hhmm, **kwargs):
        def current(now, last_success, at_startup):
            return slot_is_current(now, parent_hhmm, last_success)

        return FakeJob(name, None, after=parent, current=current, runs=self.runs, **kwargs)

    def test_startup_runs_missed_jobs_in_order_and_chained_after_parent(self):
        jobs = [
            self._slot_job("nav", "00:00"),
            self._chained("sip", "nav", "00:00"),
            self._slot_job("mfi", "01:00"),
            self._chained("watchlist", "mfi", "01:00"),
        ]
        worker = NightlyWorker(jobs, self.runner)
        worker.run_startup_pass(ist(2026, 11, 25, 9, 0))
        self.assertEqual([name for name, _ in self.runs], ["nav", "sip", "mfi", "watchlist"])

    def test_restart_does_not_re_run_current_jobs(self):
        jobs = [self._slot_job("nav", "00:00"), self._chained("sip", "nav", "00:00")]
        NightlyWorker(jobs, self.runner).run_startup_pass(ist(2026, 11, 25, 9, 0))
        self.runs.clear()
        # Simulate a process restart: a fresh store reads the same file.
        restarted = JobRunner(JobStateStore(self.store.path), use_db_lock=False)
        NightlyWorker(jobs, restarted).run_startup_pass(ist(2026, 11, 25, 9, 30))
        self.assertEqual(self.runs, [])

    def test_missed_slot_is_caught_up_next_startup(self):
        jobs = [self._slot_job("nav", "00:00")]
        NightlyWorker(jobs, self.runner).run_startup_pass(ist(2026, 11, 24, 9, 0))
        self.runs.clear()
        # PC was off at 00:00 on the 25th; next start at 08:00 catches up.
        NightlyWorker(jobs, JobRunner(JobStateStore(self.store.path), use_db_lock=False)).run_startup_pass(
            ist(2026, 11, 25, 8, 0)
        )
        self.assertEqual([name for name, _ in self.runs], ["nav"])

    def test_chained_job_waits_when_parent_failed_and_success_is_required(self):
        jobs = [
            self._slot_job("nav", "00:00", fail=True),
            self._chained("sip", "nav", "00:00", requires_parent_success=True),
        ]
        worker = NightlyWorker(jobs, self.runner)
        outcomes = worker.run_startup_pass(ist(2026, 11, 25, 9, 0))
        self.assertEqual([name for name, _ in self.runs], ["nav"])
        self.assertEqual(outcomes["sip"].status, "waiting")

    def test_due_pass_runs_only_jobs_whose_slot_arrived(self):
        jobs = [self._slot_job("nav", "00:00"), self._slot_job("security_master", "02:00")]
        worker = NightlyWorker(jobs, self.runner)
        worker.run_startup_pass(ist(2026, 11, 24, 23, 0))
        self.runs.clear()
        worker.run_due_pass(ist(2026, 11, 25, 0, 0))
        self.assertEqual([name for name, _ in self.runs], ["nav"])
        worker.run_due_pass(ist(2026, 11, 25, 2, 0))
        self.assertEqual([name for name, _ in self.runs], ["nav", "security_master"])

    def test_next_wake_is_the_earliest_upcoming_slot(self):
        jobs = [self._slot_job("nav", "00:00"), self._slot_job("security_master", "02:00")]
        worker = NightlyWorker(jobs, self.runner)
        worker.run_startup_pass(ist(2026, 11, 25, 1, 0))
        self.assertEqual(worker.next_wake(ist(2026, 11, 25, 1, 0)), ist(2026, 11, 25, 2, 0))

    def test_failed_job_is_retried_after_the_retry_interval(self):
        attempts = {"count": 0}

        def fail_once(now):
            attempts["count"] += 1
            if attempts["count"] == 1:
                raise RuntimeError("temporary")
            return {}

        job = self._slot_job("nav", "00:00")
        job.run = fail_once
        runner = JobRunner(self.store, use_db_lock=False, retry_after_minutes=60)
        worker = NightlyWorker([job], runner)
        worker.run_startup_pass(ist(2026, 11, 25, 9, 0))
        self.assertEqual(worker.next_wake(ist(2026, 11, 25, 9, 1)), ist(2026, 11, 25, 10, 0))
        worker.run_due_pass(ist(2026, 11, 25, 10, 0))
        self.assertEqual(attempts["count"], 2)
        self.assertEqual(self.store.get("nav")["last_status"], "success")


class IntervalScheduleTests(SimpleTestCase):
    def test_interval_next_run(self):
        schedule = IntervalSchedule(minutes=15)
        self.assertEqual(schedule.next_run_after(ist(2026, 11, 25, 10, 0)), ist(2026, 11, 25, 10, 15))


class JobDatabaseLockTests(SimpleTestCase):
    """Ported from the removed InvestmentAMFIScheduler lock tests."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.runner = JobRunner(JobStateStore(Path(self.directory.name) / "s.json"), use_db_lock=True)

    def test_job_is_not_run_when_another_process_holds_its_lock(self):
        from contextlib import nullcontext

        job = FakeJob("mf_nav", DailySchedule("00:00"))
        with patch("config.postgres_advisory_lock.postgres_advisory_lock", return_value=nullcontext(False)), \
                patch("django.db.close_old_connections"):
            outcome = self.runner.execute(job, now=ist(2026, 11, 25, 0, 0), reason="slot")
        self.assertEqual(outcome.status, "locked")
        self.assertEqual(job.runs, [])

    def test_job_runs_when_its_lock_is_acquired(self):
        from contextlib import nullcontext

        job = FakeJob("mf_nav", DailySchedule("00:00"))
        with patch("config.postgres_advisory_lock.postgres_advisory_lock", return_value=nullcontext(True)) as lock, \
                patch("django.db.close_old_connections"):
            outcome = self.runner.execute(job, now=ist(2026, 11, 25, 0, 0), reason="slot")
        self.assertEqual(outcome.status, "success")
        lock.assert_called_once_with("pwms_job_mf_nav")


class StartBackgroundJobsTests(SimpleTestCase):
    def test_workers_start_once_per_process(self):
        from config.scheduling import framework

        jobs = [FakeJob("prices", IntervalSchedule(15)), FakeJob("nav", DailySchedule("00:00"))]
        with patch.object(framework, "_started", False), \
                patch("config.scheduling.registry.build_jobs", return_value=jobs), \
                patch("config.scheduling.framework.threading.Thread") as thread_cls:
            self.assertTrue(framework.start_background_jobs())
            self.assertFalse(framework.start_background_jobs())
        names = sorted(call.kwargs["name"] for call in thread_cls.call_args_list)
        self.assertEqual(names, ["pwms-jobs-interval", "pwms-jobs-nightly"])
        for call in thread_cls.call_args_list:
            self.assertTrue(call.kwargs["daemon"])


class SchedulerGuardOverrideTests(SimpleTestCase):
    def test_env_override_forces_start_for_runserver_noreload(self):
        import os

        from config.scheduler_guard import should_start_background_schedulers

        argv = ["manage.py", "runserver", "--noreload"]
        with patch("config.scheduler_guard.sys.argv", argv), \
                patch("config.scheduler_guard.sys.modules", {k: v for k, v in __import__("sys").modules.items() if k != "pytest"}), \
                patch.dict(os.environ, {"PWMS_START_SCHEDULERS": "true"}, clear=False):
            os.environ.pop("PYTEST_CURRENT_TEST", None)
            os.environ.pop("PYTEST_VERSION", None)
            os.environ.pop("RUN_MAIN", None)
            self.assertTrue(should_start_background_schedulers())

    def test_without_override_noreload_starts_nothing(self):
        import os

        from config.scheduler_guard import should_start_background_schedulers

        with patch("config.scheduler_guard.sys.argv", ["manage.py", "runserver", "--noreload"]), \
                patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PWMS_START_SCHEDULERS", None)
            os.environ.pop("RUN_MAIN", None)
            self.assertFalse(should_start_background_schedulers())

    def test_env_override_can_disable_schedulers_under_waitress(self):
        import os

        from config.scheduler_guard import should_start_background_schedulers

        with patch("config.scheduler_guard.sys.argv", ["waitress-serve"]), \
                patch.dict(os.environ, {"PWMS_START_SCHEDULERS": "false"}, clear=False):
            self.assertFalse(should_start_background_schedulers())

    def test_override_never_starts_schedulers_during_tests(self):
        import os

        from config.scheduler_guard import should_start_background_schedulers

        with patch("config.scheduler_guard.sys.argv", ["manage.py", "test"]), \
                patch.dict(os.environ, {"PWMS_START_SCHEDULERS": "true"}, clear=False):
            self.assertFalse(should_start_background_schedulers())


class DisabledParentTests(SimpleTestCase):
    def test_chained_job_follows_a_disabled_parents_slot(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        runner = JobRunner(JobStateStore(Path(directory.name) / "s.json"), use_db_lock=False)
        runs = []

        def chained_current(now, last_success, at_startup):
            return slot_is_current(now, "01:00", last_success)

        parent = FakeJob("mfi", DailySchedule("01:00"), runs=runs)
        parent.enabled = False
        child = FakeJob("watchlist", None, after="mfi", current=chained_current, runs=runs)
        worker = NightlyWorker([parent, child], runner)
        worker.run_startup_pass(ist(2026, 11, 25, 0, 30))
        self.assertEqual([name for name, _ in runs], ["watchlist"])
        worker.run_due_pass(ist(2026, 11, 25, 1, 0))
        self.assertEqual([name for name, _ in runs], ["watchlist", "watchlist"])


class PerJobStartTimeTests(SimpleTestCase):
    def test_each_job_records_its_own_start_time_not_the_pass_start(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        store = JobStateStore(Path(directory.name) / "s.json")
        times = iter([ist(2026, 11, 25, 0, 0), ist(2026, 11, 25, 0, 20)])
        worker = NightlyWorker(
            [FakeJob("first", DailySchedule("00:00")), FakeJob("second", DailySchedule("00:00"))],
            JobRunner(store, use_db_lock=False),
            now_fn=lambda: next(times),
        )
        worker.run_startup_pass(ist(2026, 11, 25, 0, 0))
        self.assertEqual(store.last_success("first"), ist(2026, 11, 25, 0, 0))
        self.assertEqual(store.last_success("second"), ist(2026, 11, 25, 0, 20))
