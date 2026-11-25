"""Nightly MF Intelligence (CharlieAlpha) sync at 01:00.

Reuses MFIntelligenceSync().run() and SYNC_LOCK, so the scheduled sync and a
sync started from the UI never overlap.
"""
import logging
from datetime import timedelta

from django.utils import timezone

from config.pwms_config import get as get_pwms_config
from config.scheduling.clock import latest_daily_slot
from config.scheduling.framework import DailySchedule, JobSkipped, ScheduledJob

logger = logging.getLogger("mf_intelligence.jobs")


def _config(key, default):
    return get_pwms_config("mf_intelligence", key, default)


class MFIntelligenceJob(ScheduledJob):
    def __init__(self):
        self.hhmm = _config("auto_sync_time", "01:00")
        super().__init__(
            name="mf_intelligence",
            schedule=DailySchedule(self.hhmm),
            enabled=bool(_config("auto_sync_enabled", True)),
        )

    @staticmethod
    def _scheduled_scope():
        from mf_intelligence.constants import PLAN_CHOICES, PLAN_REGULAR

        plans = [plan for plan in _config("auto_sync_plans", [PLAN_REGULAR]) if plan in PLAN_CHOICES] or [PLAN_REGULAR]
        categories = list(_config("auto_sync_categories", None) or []) or None
        return categories, plans

    def _covers_schedule(self, scope):
        from mf_intelligence.services.sync import ALL_STEPS

        scope = scope or {}
        categories, plans = self._scheduled_scope()
        run_categories = scope.get("categories", "all")
        if run_categories != "all":
            if categories is None or not set(categories) <= set(run_categories or []):
                return False
        if not set(plans) <= set(scope.get("plans") or []):
            return False
        if not set(ALL_STEPS) <= set(scope.get("steps") or ALL_STEPS):
            return False
        return scope.get("limit") is None

    def is_current(self, now, *, last_success, at_startup):
        from mf_intelligence.models import MFSyncRun

        slot = latest_daily_slot(now, self.hhmm)
        runs = MFSyncRun.objects.filter(
            status__in=[MFSyncRun.STATUS_SUCCESS, MFSyncRun.STATUS_PARTIAL],
            finished_at__gte=slot,
        ).order_by("-finished_at")
        for run in runs:
            if self._covers_schedule(run.scope):
                return True, f"sync #{run.pk} ({run.status}) finished {timezone.localtime(run.finished_at):%d %b %H:%M}"
        return False, f"no complete sync since the {slot:%d %b %H:%M} slot"

    def run(self, now):
        from mf_intelligence.models import MFSyncRun
        from mf_intelligence.services.sync import SYNC_LOCK, MFIntelligenceSync

        stale_before = timezone.now() - timedelta(hours=_config("stale_run_hours", 6))
        if MFSyncRun.objects.filter(status=MFSyncRun.STATUS_RUNNING, started_at__gte=stale_before).exists():
            raise JobSkipped("an MF Intelligence sync started from the UI is still running")
        if not SYNC_LOCK.acquire(blocking=False):
            raise JobSkipped("another MF Intelligence sync holds the sync lock")
        try:
            categories, plans = self._scheduled_scope()
            run_record = MFIntelligenceSync().run(categories=categories, plans=plans, trigger="scheduled")
        finally:
            SYNC_LOCK.release()
        stats = getattr(run_record, "stats", None) or {}
        return {
            "status": run_record.status,
            "requests": stats.get("requests", 0),
            "errors": len(getattr(run_record, "errors", None) or []),
        }
