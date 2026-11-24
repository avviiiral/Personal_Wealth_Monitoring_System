"""Optional daily background sync (off unless enabled in pwms_config)."""

import logging
import threading
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from django.db import close_old_connections

from config.pwms_config import get as get_pwms_config
from mf_intelligence.constants import PLAN_CHOICES, PLAN_REGULAR


logger = logging.getLogger(__name__)


def _config(key, default):
    return get_pwms_config("mf_intelligence", key, default)


def next_run_after(now, run_time):
    hour, minute = (int(part) for part in str(run_time).split(":", 1))
    candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= now:
        candidate += timedelta(days=1)
    return candidate


class MFIntelligenceScheduler:
    _thread = None
    _lock = threading.Lock()

    @classmethod
    def start(cls):
        if not _config("auto_sync_enabled", False):
            return
        with cls._lock:
            if cls._thread and cls._thread.is_alive():
                return
            cls._thread = threading.Thread(target=cls._loop, name="mf-intelligence-daily-sync", daemon=True)
            cls._thread.start()
        logger.info("MF Intelligence daily sync scheduler started.")

    @classmethod
    def _loop(cls):
        from mf_intelligence.services.sync import SYNC_LOCK, MFIntelligenceSync

        zone = ZoneInfo(get_pwms_config("market", "timezone", "Asia/Kolkata"))
        while True:
            now = datetime.now(zone)
            wake = next_run_after(now, _config("auto_sync_time", "05:30"))
            time.sleep(max(1.0, (wake - now).total_seconds()))
            if not SYNC_LOCK.acquire(blocking=False):
                logger.info("MF Intelligence scheduled sync skipped: another sync is running.")
                continue
            try:
                plans = [plan for plan in _config("auto_sync_plans", [PLAN_REGULAR]) if plan in PLAN_CHOICES]
                categories = _config("auto_sync_categories", None) or None
                MFIntelligenceSync().run(categories=categories, plans=plans or [PLAN_REGULAR], trigger="scheduled")
            except Exception:  # noqa: BLE001
                logger.exception("MF Intelligence scheduled sync failed.")
            finally:
                SYNC_LOCK.release()
                close_old_connections()
