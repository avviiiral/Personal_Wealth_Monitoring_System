import logging
import threading
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from django.core.management import call_command
from django.db import close_old_connections

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")


class MutualFundUnderlyingScheduler:
    """Run underlying ingestion and Watch List universe refresh every day at 06:00 IST."""

    _started = False
    _lock = threading.Lock()

    @classmethod
    def start(cls):
        with cls._lock:
            if cls._started:
                return
            cls._started = True
            threading.Thread(target=cls._run, name="mutual-fund-underlying-scheduler", daemon=True).start()
            logger.info("Mutual-fund underlying/Watch List scheduler started for 06:00 IST daily.")

    @classmethod
    def _seconds_until_next_run(cls):
        now = datetime.now(IST)
        next_run = now.replace(hour=6, minute=0, second=0, microsecond=0)
        if now >= next_run:
            next_run += timedelta(days=1)
        return max(1, int((next_run - now).total_seconds()))

    @classmethod
    def _run(cls):
        time.sleep(15)
        while True:
            time.sleep(cls._seconds_until_next_run())
            close_old_connections()
            try:
                call_command("fetch_mf_underlying")
            except Exception:
                logger.exception("Daily mutual-fund underlying fetch failed.")
            try:
                call_command("refresh_watchlist")
            except Exception:
                logger.exception("Daily Watch List refresh failed.")
            finally:
                close_old_connections()
