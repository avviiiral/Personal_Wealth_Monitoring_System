import logging
import threading
import time
from datetime import date
from pathlib import Path

from django.conf import settings
from django.core.management import call_command
from django.db import close_old_connections


logger = logging.getLogger(__name__)

CHECK_INTERVAL_SECONDS = 30 * 60
MARKER_FILE = Path(settings.BASE_DIR) / "data" / ".last_scheduled_refresh_date"


class DailyRefreshScheduler:
    """Run the daily refresh once per calendar day while the backend runs."""

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
        time.sleep(15)

        while True:
            try:
                if not cls._already_ran_today():
                    close_old_connections()
                    call_command("run_scheduled_refresh")
                    cls._mark_ran_today()
                    logger.info("Daily refresh completed.")
            except Exception as exc:
                logger.exception("Daily refresh scheduler failed: %s", exc)
            finally:
                close_old_connections()

            time.sleep(CHECK_INTERVAL_SECONDS)
