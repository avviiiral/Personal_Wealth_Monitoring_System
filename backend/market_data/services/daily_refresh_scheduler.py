import logging
import threading
import time
from datetime import date
from pathlib import Path

from django.conf import settings
from django.core.management import call_command
from django.db import close_old_connections


logger = logging.getLogger(__name__)


# How often the scheduler wakes up to check whether today's refresh
# has already run. Deliberately much shorter than a day so a
# backend that's started late (e.g. after being off overnight)
# still catches today's run soon after starting, instead of waiting
# for a fixed clock time that may already have passed.
CHECK_INTERVAL_SECONDS = 30 * 60

# Marker file recording the last calendar date the scheduled
# refresh actually completed. Without this, every restart of the
# dev server (frequent, thanks to the autoreloader) would re-run
# AMFI NAV / Yahoo security-master lookups / Gemini news analysis
# from scratch - wasteful and, for Gemini specifically, a real
# quota concern. This makes the refresh "once per calendar day of
# uptime" rather than "once per process start".
MARKER_FILE = (
    Path(settings.BASE_DIR) / "data" / ".last_scheduled_refresh_date"
)


class DailyRefreshScheduler:
    """
    Runs market_data's run_scheduled_refresh management command
    (market prices, mutual fund NAV, security master ratios, SIP
    sync/execute, portfolio news) automatically, once per calendar
    day, for as long as the backend process is running - no
    external scheduler (Task Scheduler / cron / Celery Beat)
    required.

    Mirrors MarketPriceScheduler's pattern in this same app: a
    daemon thread started from AppConfig.ready(), guarded so it
    only starts once in the real serving process even under
    Django's autoreloading dev server.
    """

    _started = False
    _lock = threading.Lock()

    @classmethod
    def start(cls):

        with cls._lock:

            if cls._started:
                return

            cls._started = True

            thread = threading.Thread(
                target=cls._run,
                name="daily-refresh-scheduler",
                daemon=True,
            )

            thread.start()

            logger.info("Daily refresh scheduler started.")

    @classmethod
    def _already_ran_today(cls):

        if not MARKER_FILE.exists():
            return False

        try:
            last_run = MARKER_FILE.read_text().strip()
        except OSError:
            logger.exception("Could not read daily refresh marker file.")
            return False

        return last_run == date.today().isoformat()

    @classmethod
    def _mark_ran_today(cls):

        try:
            MARKER_FILE.parent.mkdir(parents=True, exist_ok=True)
            MARKER_FILE.write_text(date.today().isoformat())
        except OSError:
            logger.exception("Could not write daily refresh marker file.")

    @classmethod
    def _run(cls):

        # Give Django time to finish startup.
        time.sleep(15)

        while True:

            try:

                if not cls._already_ran_today():

                    close_old_connections()

                    call_command("run_scheduled_refresh")

                    cls._mark_ran_today()

                    logger.info("Daily refresh completed.")

            except Exception as exc:

                logger.exception(
                    "Daily refresh scheduler failed: %s",
                    exc,
                )

            finally:

                close_old_connections()

            time.sleep(CHECK_INTERVAL_SECONDS)