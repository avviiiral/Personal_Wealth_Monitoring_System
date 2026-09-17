import logging
import threading
import time

from django.core.management import call_command
from django.db import close_old_connections

logger = logging.getLogger(__name__)

REFRESH_INTERVAL_SECONDS = 24 * 60 * 60
STARTUP_DELAY_SECONDS = 15


class SecurityMetricsScheduler:
    """Refresh stock metrics once at startup and every 24 hours."""

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
                name="security-metrics-scheduler",
                daemon=True,
            )
            thread.start()
            logger.info("Security metrics scheduler started.")

    @classmethod
    def _refresh(cls):
        close_old_connections()
        try:
            call_command("refresh_security_metrics")
            logger.info("Security metrics refresh completed.")
        except Exception:
            logger.exception("Security metrics refresh failed.")
        finally:
            close_old_connections()

    @classmethod
    def _run(cls):
        # Give Django startup/migrations and the HTTP server a moment to settle.
        time.sleep(STARTUP_DELAY_SECONDS)

        while True:
            cls._refresh()
            time.sleep(REFRESH_INTERVAL_SECONDS)
