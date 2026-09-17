import logging
import threading
import time

from django.apps import AppConfig

from config.database_scheduler_lock import DATABASE_SCHEDULER_LOCK


logger = logging.getLogger(__name__)


class WatchlistConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "watchlist"

    _refresh_thread = None
    _refresh_lock = threading.Lock()
    REFRESH_INTERVAL_SECONDS = 24 * 60 * 60

    def ready(self):
        # Django's autoreloader starts the application twice. Only start the
        # worker in the actual serving process, not in the autoreloader parent.
        import os

        if os.environ.get("RUN_MAIN") != "true":
            return
        if self._refresh_thread and self._refresh_thread.is_alive():
            return

        self._refresh_thread = threading.Thread(
            target=self._watchlist_refresh_loop,
            name="watchlist-24h-refresh",
            daemon=True,
        )
        self._refresh_thread.start()

    @classmethod
    def _watchlist_refresh_loop(cls):
        # Give Django time to finish application initialization before the
        # first network/database refresh.
        time.sleep(2)

        while True:
            try:
                from watchlist.services.pms import APMIPMSDiscoveryService
                from watchlist.services.universe import AMFIUniverseService

                logger.info("Automatic Watch List refresh started.")
                with DATABASE_SCHEDULER_LOCK:
                    mf_result = AMFIUniverseService.refresh()
                    logger.info("Automatic Mutual Fund refresh completed: %s", mf_result)

                    pms_result = APMIPMSDiscoveryService.refresh()
                    logger.info("Automatic PMS refresh completed: %s", pms_result)

                logger.info("Automatic Watch List refresh completed successfully.")
            except Exception:
                # A failed refresh must not kill the background worker. It will
                # retry after the next 24-hour interval while the backend runs.
                logger.exception("Automatic Watch List refresh failed.")

            # The 24-hour interval starts after the refresh attempt completes,
            # so the next update is approximately 24 hours after this update.
            time.sleep(cls.REFRESH_INTERVAL_SECONDS)
