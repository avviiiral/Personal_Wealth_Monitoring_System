import logging
import threading
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from django.core.management import call_command
from django.db import close_old_connections

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")


class WatchListScheduler:
    _started = False
    _lock = threading.Lock()

    @classmethod
    def start(cls):
        with cls._lock:
            if cls._started:
                return
            cls._started = True
            threading.Thread(target=cls._run, name="watch-list-scheduler", daemon=True).start()
            logger.info("Watch List scheduler started for 06:30 IST daily.")

    @classmethod
    def _seconds_until_next_run(cls):
        now = datetime.now(IST)
        target = now.replace(hour=6, minute=30, second=0, microsecond=0)
        if now >= target:
            target += timedelta(days=1)
        return max(1, int((target - now).total_seconds()))

    @classmethod
    def _run(cls):
        time.sleep(20)
        while True:
            time.sleep(cls._seconds_until_next_run())
            close_old_connections()
            try:
                call_command("refresh_watchlist")
            except Exception:
                logger.exception("Daily Watch List refresh failed.")
            finally:
                close_old_connections()
