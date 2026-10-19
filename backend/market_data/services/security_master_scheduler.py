import logging
import threading
import time

from django.db import close_old_connections

logger = logging.getLogger(__name__)

STARTUP_DELAY_SECONDS = 3
REFRESH_INTERVAL_SECONDS = 24 * 60 * 60
RETRY_INTERVAL_SECONDS = 15 * 60


class SecurityMasterScheduler:
    """Refresh the ISIN security master on startup and once per day.

    Runs off the request thread so slow or unavailable external data sources
    cannot prevent Django from serving requests. Failed refreshes are retried
    after a short delay; existing workbook data remains in place.
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
                name="security-master-scheduler",
                daemon=True,
            )
            thread.start()
            logger.info("Security master scheduler started.")

    @classmethod
    def refresh(cls):
        """Run one safe refresh and reload resolver mappings after success."""
        close_old_connections()
        try:
            from market_data.services.security_master_generator import (
                SecurityMasterGenerator,
            )
            from market_data.services.security_resolver import SecurityResolver

            result = SecurityMasterGenerator.generate()
            SecurityResolver.reload_security_master()

            logger.info(
                "Security master refresh completed: total=%s, resolved=%s, "
                "unresolved=%s, non_yahoo=%s, output=%s",
                result.get("total"),
                result.get("resolved"),
                result.get("unresolved"),
                result.get("non_yahoo"),
                result.get("output"),
            )
            return True
        except Exception:
            logger.exception(
                "Automatic security master refresh failed; keeping the "
                "existing workbook and retrying later."
            )
            return False
        finally:
            close_old_connections()

    @classmethod
    def _run(cls):
        time.sleep(STARTUP_DELAY_SECONDS)

        while True:
            succeeded = cls.refresh()
            time.sleep(
                REFRESH_INTERVAL_SECONDS
                if succeeded
                else RETRY_INTERVAL_SECONDS
            )
