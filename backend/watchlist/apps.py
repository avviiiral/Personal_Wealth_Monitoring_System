import logging
import threading
import time

from django.apps import AppConfig
from django.db import close_old_connections


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
                from watchlist.services.background_refresh import BackgroundWatchListRefreshService

                # The worker fetches/parses external data first and only then
                # performs short autocommit ORM writes. No global scheduler
                # lock is held while AMFI/APMI/network calls are in flight.
                result = BackgroundWatchListRefreshService.refresh()
                logger.info("Automatic Watch List refresh completed: %s", result)
            except Exception:
                logger.exception("Automatic Watch List refresh failed.")
            finally:
                close_old_connections()

            # The 24-hour interval starts after the refresh attempt completes.
            time.sleep(cls.REFRESH_INTERVAL_SECONDS)
