"""Job definitions, the job runner and the two worker loops."""
import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from config.scheduling import clock

logger = logging.getLogger(__name__)

# Outcome statuses -----------------------------------------------------------
SUCCESS = "success"          # run() completed
FAILED = "failed"            # run() raised
CURRENT = "current"          # is_current() said there is nothing to do
SKIPPED = "skipped"          # run() raised JobSkipped (busy, prerequisite missing)
GAVE_UP = "gave_up"          # too many failed attempts for this slot
LOCKED = "locked"            # another process holds the job's database lock
WAITING = "waiting"          # chained job whose parent did not succeed
DISABLED = "disabled"

RETRYABLE = {FAILED, SKIPPED}


class JobSkipped(Exception):
    """Raised by run() when the job cannot run now (not a failure)."""


class IntervalSchedule:
    def __init__(self, minutes):
        self.minutes = int(minutes)

    def next_run_after(self, started):
        return started + timedelta(minutes=self.minutes)

    def describe(self):
        return f"every {self.minutes} min"


class DailySchedule:
    def __init__(self, hhmm):
        clock.parse_hhmm(hhmm)
        self.hhmm = str(hhmm)

    def latest_slot(self, now):
        return clock.latest_daily_slot(now, self.hhmm)

    def next_slot(self, now):
        return clock.next_daily_slot(now, self.hhmm)

    def describe(self):
        return f"daily {self.hhmm}"


def slot_is_current(now, hhmm, last_success):
    """Current when the job succeeded at or after its latest daily slot."""
    slot = clock.latest_daily_slot(now, hhmm)
    if last_success is not None and clock.localize(last_success) >= slot:
        return True, f"succeeded at {clock.localize(last_success):%Y-%m-%d %H:%M}, after the {slot:%Y-%m-%d %H:%M} slot"
    return False, f"no success since the {slot:%Y-%m-%d %H:%M} slot"


def max_age_is_current(now, hhmm, last_success, *, at_startup, max_age_hours, min_gap_hours):
    """Rule for jobs that run nightly but only need to be "fresh within N hours".

    At startup the job is current when it succeeded less than ``max_age_hours``
    ago. At its nightly slot it runs unless it already ran after that slot or
    a startup catch-up ran it less than ``min_gap_hours`` ago. Comparing with
    the slot (not "24 h since the last run") keeps the start time from
    drifting later every day.
    """
    if last_success is None:
        return False, "never succeeded"
    last_success = clock.localize(last_success)
    slot = clock.latest_daily_slot(now, hhmm)
    if last_success >= slot:
        return True, f"succeeded at {last_success:%Y-%m-%d %H:%M}, after the {slot:%H:%M} slot"
    age = now - last_success
    limit_hours = max_age_hours if at_startup else min_gap_hours
    if age < timedelta(hours=limit_hours):
        return True, f"succeeded {age.total_seconds() / 3600:.1f} h ago (limit {limit_hours} h)"
    return False, f"last success {age.total_seconds() / 3600:.1f} h ago"


class ScheduledJob:
    """Base class. Subclasses implement is_current() and run()."""

    def __init__(self, *, name, schedule, after=None, requires_parent_success=False, enabled=True,
                 log_current_at_info=True):
        self.name = name
        self.schedule = schedule
        self.after = after
        self.requires_parent_success = requires_parent_success
        self.enabled = enabled
        self.log_current_at_info = log_current_at_info

    def is_current(self, now, *, last_success, at_startup):  # pragma: no cover - overridden
        return False, "no currency rule"

    def run(self, now):  # pragma: no cover - overridden
        raise NotImplementedError

    def describe(self):
        if self.after:
            return f"{self.name} (after {self.after})"
        return f"{self.name} ({self.schedule.describe()})"


@dataclass
class JobOutcome:
    name: str
    status: str
    reason: str = ""
    counts: dict = field(default_factory=dict)
    error: str = ""
    started_at: datetime = None
    duration: float = 0.0


def _format_counts(counts):
    parts = []
    for key, value in (counts or {}).items():
        if isinstance(value, (dict, list, tuple, set)):
            value = len(value)
        parts.append(f"{key}={value}")
    return " ".join(parts)


class JobRunner:
    def __init__(self, store, *, use_db_lock=True, max_attempts_per_slot=3, retry_after_minutes=60):
        self.store = store
        self.use_db_lock = use_db_lock
        self.max_attempts_per_slot = max(1, int(max_attempts_per_slot))
        self.retry_after = timedelta(minutes=int(retry_after_minutes))

    @staticmethod
    def _default_slot(job, now):
        if isinstance(job.schedule, DailySchedule):
            return job.schedule.latest_slot(now)
        return None

    def _db_lock(self, job):
        if not self.use_db_lock:
            from contextlib import nullcontext

            return nullcontext(True)
        from config.postgres_advisory_lock import postgres_advisory_lock

        return postgres_advisory_lock(f"pwms_job_{job.name}")

    def execute(self, job, *, now, reason, force=False, slot=None, at_startup=None):
        """Run one job once. Never raises."""
        if at_startup is None:
            at_startup = reason == "startup"
        if slot is None:
            slot = self._default_slot(job, now)

        if not job.enabled and not force:
            return JobOutcome(job.name, DISABLED, "disabled in pwms_config.json")

        try:
            if not force:
                current, why = job.is_current(
                    now, last_success=self.store.last_success(job.name), at_startup=at_startup,
                )
                if current:
                    level = logging.INFO if (job.log_current_at_info or at_startup) else logging.DEBUG
                    logger.log(level, "job %s current, not run (%s; reason=%s)", job.name, why, reason)
                    return JobOutcome(job.name, CURRENT, why)
                if slot is not None and self.store.slot_attempts(job.name, slot) >= self.max_attempts_per_slot:
                    logger.warning(
                        "job %s not run: %s failed attempts already for the %s slot",
                        job.name, self.max_attempts_per_slot, f"{slot:%Y-%m-%d %H:%M}",
                    )
                    return JobOutcome(job.name, GAVE_UP, "attempt limit reached for this slot")
        except Exception as exc:  # noqa: BLE001 - a broken check must not kill the worker
            logger.exception("job %s currency check failed; running it anyway.", job.name)
            why = f"currency check failed: {exc}"

        started_at = now
        started_clock = time.monotonic()
        counts = {}
        error = ""
        status = SUCCESS
        failure_info = None
        logger.info("job %s started (reason=%s)", job.name, reason)
        try:
            from django.db import close_old_connections

            close_old_connections()
            with self._db_lock(job) as acquired:
                if not acquired:
                    status = LOCKED
                    error = "another process is running this job"
                else:
                    counts = job.run(now) or {}
        except JobSkipped as exc:
            status = SKIPPED
            error = str(exc)
        except Exception as exc:  # noqa: BLE001 - exceptions never kill a worker
            status = FAILED
            error = f"{type(exc).__name__}: {exc}"
            failure_info = (type(exc), exc, exc.__traceback__)
        finally:
            try:
                from django.db import close_old_connections

                close_old_connections()
            except Exception:  # noqa: BLE001
                pass

        duration = time.monotonic() - started_clock
        finished_at = started_at + timedelta(seconds=duration)
        counts_text = _format_counts(counts)
        if status == FAILED:
            logger.error(
                "job %s finished status=failed duration=%.1fs %s error=%s",
                job.name, duration, counts_text, error, exc_info=failure_info,
            )
        elif status in (SKIPPED, LOCKED):
            logger.warning("job %s finished status=%s duration=%.1fs reason=%s", job.name, status, duration, error)
        else:
            logger.info("job %s finished status=%s duration=%.1fs %s", job.name, status, duration, counts_text)

        if status in (SUCCESS, FAILED):
            self.store.record(
                job.name, status=status, started_at=started_at, finished_at=finished_at,
                reason=reason, counts=counts, error=error, slot=slot,
            )
        return JobOutcome(job.name, status, reason, counts, error, started_at, duration)


class NightlyWorker:
    """Runs daily and chained jobs one after another, in list order."""

    def __init__(self, jobs, runner, *, now_fn=None):
        self.jobs = list(jobs)
        self.by_name = {job.name: job for job in self.jobs}
        self.runner = runner
        # Jobs run one after another, so each job's start time is read when
        # it starts (a pass can take an hour). Tests pass a fixed clock.
        self.now_fn = now_fn
        self._evaluated_slot = {}
        self._retry_at = {}
        self._last_outcome = {}

    def _daily_schedule(self, job):
        seen = set()
        while job.after and job.name not in seen:
            seen.add(job.name)
            job = self.by_name[job.after]
        return job.schedule if isinstance(job.schedule, DailySchedule) else None

    def _slot(self, job, now):
        schedule = self._daily_schedule(job)
        return schedule.latest_slot(now) if schedule else None

    def _after_run(self, job, outcome, now, slot):
        self._evaluated_slot[job.name] = slot
        self._last_outcome[job.name] = (slot, outcome.status)
        if outcome.status in RETRYABLE:
            self._retry_at[job.name] = now + self.runner.retry_after
        else:
            self._retry_at.pop(job.name, None)

    def _parent_status(self, job, outcomes, slot):
        parent = outcomes.get(job.after)
        if parent is not None:
            return parent.status
        previous_slot, status = self._last_outcome.get(job.after, (None, None))
        return status if previous_slot == slot else None

    def _run_chain(self, now, reason, select):
        outcomes = {}
        for job in self.jobs:
            slot = self._slot(job, now)
            if not job.enabled:
                # A disabled parent still "passes" at its slot so that jobs
                # chained after it keep their schedule.
                if select is None or slot != self._evaluated_slot.get(job.name):
                    outcomes[job.name] = JobOutcome(job.name, DISABLED, "disabled in pwms_config.json")
                    self._evaluated_slot[job.name] = slot
                continue
            retry_due = self._retry_at.get(job.name) is not None and now >= self._retry_at[job.name]
            if job.after:
                if job.after not in outcomes and not (select is None or retry_due):
                    continue
                parent_status = self._parent_status(job, outcomes, slot)
                if job.requires_parent_success and parent_status not in (SUCCESS, CURRENT, DISABLED):
                    outcome = JobOutcome(job.name, WAITING, f"{job.after} did not complete ({parent_status})")
                    logger.info("job %s waiting: %s", job.name, outcome.reason)
                    outcomes[job.name] = outcome
                    # No retry of its own: it runs again right after the
                    # parent's next successful attempt.
                    self._evaluated_slot[job.name] = slot
                    self._retry_at.pop(job.name, None)
                    continue
            elif select is not None and not select(job, slot, retry_due):
                continue
            run_reason = "retry" if (select is not None and retry_due and slot == self._evaluated_slot.get(job.name)) else reason
            job_now = self.now_fn() if self.now_fn is not None else now
            outcome = self.runner.execute(job, now=job_now, reason=run_reason, slot=slot, at_startup=(reason == "startup"))
            outcomes[job.name] = outcome
            self._after_run(job, outcome, now, slot)
        return outcomes

    def run_startup_pass(self, now):
        return self._run_chain(now, "startup", None)

    def run_due_pass(self, now):
        def select(job, slot, retry_due):
            return retry_due or (slot is not None and slot != self._evaluated_slot.get(job.name))

        return self._run_chain(now, "slot", select)

    def next_wake(self, now):
        candidates = []
        for job in self.jobs:
            if not job.after and isinstance(job.schedule, DailySchedule):
                candidates.append(job.schedule.next_slot(now))
            retry = self._retry_at.get(job.name)
            if retry is not None:
                candidates.append(max(retry, now))
        return min(candidates) if candidates else now + timedelta(hours=1)

    def run_forever(self, *, startup_delay_seconds=10, now_fn=clock.now, sleep=time.sleep, max_sleep_seconds=60):
        sleep(startup_delay_seconds)
        try:
            self.run_startup_pass(now_fn())
        except Exception:  # noqa: BLE001
            logger.exception("Nightly startup catch-up pass failed.")
        while True:
            try:
                wake = self.next_wake(now_fn())
                # Sleep in short steps and re-read the wall clock, so a PC
                # that resumes from sleep catches up at once instead of
                # oversleeping by the time it was suspended.
                while True:
                    remaining = (wake - now_fn()).total_seconds()
                    if remaining <= 0:
                        break
                    sleep(min(max_sleep_seconds, remaining))
                self.run_due_pass(now_fn())
            except Exception:  # noqa: BLE001 - keep the worker alive
                logger.exception("Nightly scheduler pass failed.")
                sleep(max_sleep_seconds)


class IntervalWorker:
    """Runs interval jobs (the 15-minute price refresh)."""

    def __init__(self, jobs, runner):
        self.jobs = [job for job in jobs if job.enabled]
        self.runner = runner
        self._next_run = {}

    def run_pass(self, now, *, reason):
        outcomes = {}
        for job in self.jobs:
            due_at = self._next_run.get(job.name)
            if due_at is not None and now < due_at:
                continue
            outcome = self.runner.execute(job, now=now, reason=reason, at_startup=(reason == "startup"))
            outcomes[job.name] = outcome
            self._next_run[job.name] = job.schedule.next_run_after(now)
        return outcomes

    def next_wake(self, now):
        return min(self._next_run.values()) if self._next_run else now

    def run_forever(self, *, startup_delay_seconds=10, now_fn=clock.now, sleep=time.sleep, max_sleep_seconds=60):
        sleep(startup_delay_seconds)
        reason = "startup"
        while True:
            try:
                self.run_pass(now_fn(), reason=reason)
                reason = "interval"
                wake = self.next_wake(now_fn())
                while True:
                    remaining = (wake - now_fn()).total_seconds()
                    if remaining <= 0:
                        break
                    sleep(min(max_sleep_seconds, remaining))
            except Exception:  # noqa: BLE001 - keep the worker alive
                logger.exception("Interval scheduler pass failed.")
                sleep(max_sleep_seconds)


_start_lock = threading.Lock()
_started = False


def scheduler_setting(key, default):
    from config.pwms_config import get as get_pwms_config

    return get_pwms_config("scheduler", key, default)


def build_runner(store=None):
    from config.scheduling.state import JobStateStore

    return JobRunner(
        store or JobStateStore(),
        max_attempts_per_slot=scheduler_setting("max_attempts_per_slot", 3),
        retry_after_minutes=scheduler_setting("failure_retry_minutes", 60),
    )


def start_background_jobs():
    """Start the interval and nightly worker threads once per process."""
    global _started
    from config.scheduling.registry import build_jobs

    with _start_lock:
        if _started:
            return False
        _started = True

    jobs = build_jobs()
    runner = build_runner()
    interval_jobs = [job for job in jobs if isinstance(job.schedule, IntervalSchedule) and job.enabled]
    # Disabled nightly jobs stay in the list so jobs chained after them keep
    # their slot; the worker never runs them.
    nightly_jobs = [job for job in jobs if not isinstance(job.schedule, IntervalSchedule)]
    delay = scheduler_setting("startup_delay_seconds", 10)

    if interval_jobs:
        threading.Thread(
            target=IntervalWorker(interval_jobs, runner).run_forever,
            kwargs={"startup_delay_seconds": delay},
            name="pwms-jobs-interval",
            daemon=True,
        ).start()
    if any(job.enabled for job in nightly_jobs):
        threading.Thread(
            target=NightlyWorker(nightly_jobs, runner, now_fn=clock.now).run_forever,
            kwargs={"startup_delay_seconds": delay},
            name="pwms-jobs-nightly",
            daemon=True,
        ).start()
    logger.info(
        "Background jobs started. interval: %s. nightly: %s.",
        ", ".join(job.describe() for job in interval_jobs) or "none",
        ", ".join(job.describe() + ("" if job.enabled else " [disabled]") for job in nightly_jobs) or "none",
    )
    return True
