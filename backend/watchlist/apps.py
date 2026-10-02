import logging
import threading
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from django.apps import AppConfig
from django.db import close_old_connections


logger = logging.getLogger(__name__)


class WatchlistConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "watchlist"

    _refresh_thread = None
    _refresh_lock = threading.Lock()
    REFRESH_INTERVAL_SECONDS = 24 * 60 * 60
    IST = ZoneInfo("Asia/Kolkata")

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
        # Bootstrap shared benchmark history asynchronously so a fresh clone
        # never requires a manual benchmark download before the Watch List
        # comparison chart can be opened.
        try:
            from watchlist.services.benchmark import BenchmarkPerformanceService

            result = BenchmarkPerformanceService.ensure_benchmark_master_history()
            logger.info("Automatic benchmark master bootstrap completed: %s", result)
        except Exception:
            logger.exception("Automatic benchmark master bootstrap failed.")
        finally:
            close_old_connections()

        now = datetime.now(cls.IST)
        next_run = now.replace(hour=6, minute=0, second=0, microsecond=0)
        if now >= next_run:
            next_run += timedelta(days=1)
        time.sleep(max(1, (next_run - now).total_seconds()))

        while True:
            try:
                from watchlist.services.benchmark import BenchmarkPerformanceService
                from watchlist.services.background_refresh import BackgroundWatchListRefreshService

                # Keep the shared benchmark master current as part of the same
                # daily background cycle. The service skips a download when
                # existing history already has sufficient recent coverage.
                benchmark_result = BenchmarkPerformanceService.ensure_benchmark_master_history()
                logger.info("Automatic benchmark master refresh completed: %s", benchmark_result)

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
