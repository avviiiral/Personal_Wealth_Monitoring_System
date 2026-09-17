import logging
import os
import threading
import time

from django.db import close_old_connections

from config.database_scheduler_lock import DATABASE_SCHEDULER_LOCK
from portfolio_news.services.pipeline import run_portfolio_news_monitor


logger = logging.getLogger(__name__)

DEFAULT_INTERVAL_SECONDS = 1800


def _get_interval_seconds() -> int:
    try:
        return int(os.environ.get("NEWS_MONITOR_INTERVAL", DEFAULT_INTERVAL_SECONDS))
    except (TypeError, ValueError):
        return DEFAULT_INTERVAL_SECONDS


class PortfolioNewsScheduler:
    """Run portfolio news monitoring while the backend process is running."""

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
                name="portfolio-news-scheduler",
                daemon=True,
            )
            thread.start()

            interval_seconds = _get_interval_seconds()
            logger.info(
                "Portfolio news scheduler started. Run interval: %s seconds.",
                interval_seconds,
            )

    @classmethod
    def _run(cls):
        time.sleep(10)
        interval_seconds = _get_interval_seconds()

        while True:
            try:
                close_old_connections()
                # News processing also writes alerts/articles. Serialize it
                # with the other background database-heavy schedulers when
                # SQLite is used for local development.
                with DATABASE_SCHEDULER_LOCK:
                    stats = run_portfolio_news_monitor()

                logger.info(
                    "Portfolio news monitor run complete: users=%s, holdings=%s, "
                    "articles_matched=%s, new_articles=%s, alerts_created=%s, "
                    "notifications_sent=%s",
                    stats["users_processed"],
                    stats["holdings_processed"],
                    stats["articles_matched"],
                    stats["articles_stored_new"],
                    stats["alerts_created"],
                    stats["notifications_sent"],
                )
            except Exception as exc:
                logger.exception(
                    "Portfolio news scheduler run failed: %s. Will retry after the next interval.",
                    exc,
                )
            finally:
                close_old_connections()

            time.sleep(interval_seconds)
