"""In-process scheduler for the masterdata pipeline.

Started from MutualFundsConfig.ready() through config.scheduler_guard, the
same way PWMS's other background schedulers start: it runs under
``manage.py runserver`` (worker process only) and under waitress/uvicorn, and
never for one-off management commands or tests.

On backend start it runs every job once, in dependency order, unless the job
already ran recently (per-job cooldown) so autoreloads/restarts do not
hammer AMFI; the one-time NAV backfill runs on startup only until it has
succeeded once. After that, jobs follow the IST timeline in JOB_SPECS.
Retry slots simply re-run the job: every job is idempotent and exits early
when its data is unchanged/already loaded/not yet published.

Environment:
    PWMS_MF_PIPELINE_ENABLED=0           disable the scheduler entirely
    PWMS_MF_PIPELINE_RUN_ON_STARTUP=0    skip the startup run (timeline only)
    PWMS_MF_PIPELINE_STARTUP_DELAY_SECONDS  wait before the startup run (default 60)
    PWMS_MF_BACKFILL_SCOPE               held (default) | growth | all
"""
from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")

ALL_DAYS = frozenset(range(7))
MON_FRI = frozenset(range(5))
MON_SAT = frozenset(range(6))
TUE_SAT = frozenset(range(1, 6))
TUE_SUN = frozenset(range(1, 7))
SUNDAY = frozenset({6})
MONDAY = frozenset({0})


@dataclass(frozen=True)
class Rule:
    hour: int
    minute: int
    weekdays: frozenset = ALL_DAYS          # 0 = Monday
    days_of_month: tuple | None = None       # inclusive (first, last)

    def matches(self, day) -> bool:
        if day.weekday() not in self.weekdays:
            return False
        if self.days_of_month and not self.days_of_month[0] <= day.day <= self.days_of_month[1]:
            return False
        return True


@dataclass(frozen=True)
class JobSpec:
    name: str
    rules: tuple
    startup_cooldown: timedelta | None  # None: run on startup only until first success
    description: str


JOB_SPECS = {
    spec.name: spec for spec in (
        JobSpec("mf_sync_nav", (
            Rule(23, 30, MON_SAT),
            Rule(9, 30, TUE_SUN), Rule(11, 0, TUE_SUN),  # next-day retries for late FoF/overseas NAVs
        ), timedelta(hours=2), "Daily NAV + scheme master (NAVAll.txt)"),
        JobSpec("mf_backfill_nav", (), None, "One-time historical NAV backfill (mfapi.in, held schemes)"),
        JobSpec("mf_nav_gap_check", (Rule(3, 0, SUNDAY),), timedelta(days=3), "Weekly NAV gap check (mfapi.in)"),
        JobSpec("mf_sync_ter", (Rule(6, 0, MONDAY),), timedelta(days=1), "Weekly TER (AMFI via amfipy)"),
        JobSpec("mf_sync_aum", (Rule(6, 0, ALL_DAYS, (10, 20)),), timedelta(hours=12), "AUM: 10th, daily retry to 20th"),
        JobSpec("mf_sync_risk", (Rule(6, 0, ALL_DAYS, (10, 20)),), timedelta(hours=12), "AMFI risk parameters + tracking error: 10th, daily retry to 20th"),
        JobSpec("mf_sync_risk_free", (Rule(10, 0, ALL_DAYS),), timedelta(hours=12), "91D T-bill: Thursday, daily retry until updated"),
        JobSpec("mf_sync_benchmark", (Rule(20, 0, MON_FRI), Rule(9, 0, TUE_SAT)), timedelta(hours=6), "Benchmark TRI: trading days 20:00, retry 09:00"),
        JobSpec("mf_sync_holdings", (Rule(5, 0, ALL_DAYS, (11, 20)),), timedelta(hours=12), "AMC holdings: 11th, daily to 15th; debt mid-month to 20th"),
        JobSpec("mf_calc_ratios", (Rule(0, 30),), timedelta(hours=1), "Daily snapshot build + calculated ratios"),
    )
}
STARTUP_ORDER = (
    "mf_sync_nav", "mf_backfill_nav", "mf_nav_gap_check", "mf_sync_ter", "mf_sync_aum",
    "mf_sync_risk", "mf_sync_risk_free", "mf_sync_benchmark", "mf_sync_holdings", "mf_calc_ratios",
)


def _env_flag(name, default=True) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().casefold() not in {"0", "false", "no", "off"}


def pipeline_enabled() -> bool:
    return _env_flag("PWMS_MF_PIPELINE_ENABLED", True)


def slots_between(spec: JobSpec, start: datetime, end: datetime) -> list[datetime]:
    """Scheduled IST datetimes with start < slot <= end."""
    start, end = start.astimezone(IST), end.astimezone(IST)
    slots = set()
    day = start.date()
    while day <= end.date():
        for rule in spec.rules:
            if rule.matches(day):
                slot = datetime(day.year, day.month, day.day, rule.hour, rule.minute, tzinfo=IST)
                if start < slot <= end:
                    slots.add(slot)
        day += timedelta(days=1)
    return sorted(slots)


def next_slot(spec: JobSpec, now: datetime) -> datetime | None:
    for days in (7, 45):
        slots = slots_between(spec, now, now + timedelta(days=days))
        if slots:
            return slots[0]
    return None


def startup_plan(*, now: datetime, last_success) -> list[str]:
    """Jobs to run at backend start, in dependency order."""
    plan = []
    for name in STARTUP_ORDER:
        spec = JOB_SPECS[name]
        last = last_success(name)
        if last is None:
            plan.append(name)
        elif spec.startup_cooldown is not None and now - last >= spec.startup_cooldown:
            plan.append(name)
    return plan


def _runners():
    from . import jobs

    return {
        "mf_sync_nav": lambda: [jobs.sync_nav()],
        "mf_backfill_nav": lambda: [jobs.backfill_nav(source="mfapi", scope=os.environ.get("PWMS_MF_BACKFILL_SCOPE", "held"))],
        "mf_nav_gap_check": lambda: [jobs.nav_gap_check()],
        "mf_sync_ter": lambda: [jobs.sync_ter()],
        "mf_sync_aum": lambda: [jobs.sync_aum()],
        "mf_sync_risk": lambda: [jobs.sync_amfi_risk(), jobs.sync_tracking_error()],
        "mf_sync_risk_free": lambda: [jobs.sync_risk_free()],
        "mf_sync_benchmark": lambda: [jobs.sync_benchmark()],
        "mf_sync_holdings": lambda: [jobs.sync_holdings()],
        "mf_calc_ratios": lambda: [jobs.calc_ratios()],
    }


def execute(name: str):
    """Run one job under a cross-process lock (PostgreSQL advisory lock; no-op on SQLite)."""
    from django.db import close_old_connections

    from config.postgres_advisory_lock import postgres_advisory_lock

    close_old_connections()
    runs = []
    try:
        with postgres_advisory_lock(f"mf_pipeline:{name}") as acquired:
            if not acquired:
                logger.info("Masterdata job %s already running in another process; skipped.", name)
                return []
            runs = _runners()[name]()
            for run in runs:
                logger.info("Masterdata job %s finished: %s %s", run.command, run.status,
                            (run.metadata or {}).get("skip_reason", ""))
    except Exception:
        logger.exception("Masterdata job %s crashed.", name)
    finally:
        close_old_connections()
    return runs


class MFPipelineScheduler:
    POLL_SECONDS = 30
    _started = False
    _lock = threading.Lock()

    @classmethod
    def start(cls):
        if not pipeline_enabled():
            logger.info("Mutual-fund masterdata pipeline disabled (PWMS_MF_PIPELINE_ENABLED=0).")
            return
        with cls._lock:
            if cls._started:
                return
            cls._started = True
        threading.Thread(target=cls._run, name="mf-masterdata-pipeline", daemon=True).start()
        logger.info("Mutual-fund masterdata pipeline scheduler started.")

    @classmethod
    def _run(cls):
        from .runlog import last_success_at

        time.sleep(max(0, int(os.environ.get("PWMS_MF_PIPELINE_STARTUP_DELAY_SECONDS", "60"))))
        if _env_flag("PWMS_MF_PIPELINE_RUN_ON_STARTUP", True):
            try:
                plan = startup_plan(now=datetime.now(IST), last_success=last_success_at)
            except Exception:
                logger.exception("Could not compute masterdata startup plan; running every job.")
                plan = list(STARTUP_ORDER)
            logger.info("Masterdata pipeline startup run: %s", ", ".join(plan) or "nothing due")
            for name in plan:
                execute(name)
        last_check = datetime.now(IST)
        while True:
            time.sleep(cls.POLL_SECONDS)
            now = datetime.now(IST)
            due = [name for name in STARTUP_ORDER if slots_between(JOB_SPECS[name], last_check, now)]
            last_check = now
            for name in due:
                execute(name)
