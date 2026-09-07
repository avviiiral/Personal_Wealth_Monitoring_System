import logging
import os
import threading
import time

from django.db import close_old_connections

from portfolio_news.services.pipeline import run_portfolio_news_monitor


logger = logging.getLogger(__name__)


DEFAULT_INTERVAL_SECONDS = 1800  # 30 minutes - same default the
# monitor_portfolio_news --loop command uses, and configurable the
# same way (NEWS_MONITOR_INTERVAL), so switching between "run via
# runserver" and "run via manage.py monitor_portfolio_news --loop"
# never means re-learning a different setting.


def _get_interval_seconds() -> int:
    try:
        return int(
            os.environ.get(
                "NEWS_MONITOR_INTERVAL",
                DEFAULT_INTERVAL_SECONDS,
            )
        )
    except (TypeError, ValueError):
        return DEFAULT_INTERVAL_SECONDS


class PortfolioNewsScheduler:
    """
    Runs the portfolio news monitoring pipeline automatically for
    as long as `runserver` is running - no external Task Scheduler/
    cron entry needed. Mirrors
    market_data.services.market_price_scheduler.MarketPriceScheduler
    exactly: a daemon background thread, started once from
    PortfolioNewsConfig.ready() (guarded against Django's dev-server
    autoreloader starting the app twice), looping forever with
    close_old_connections() around each run so a long-lived thread
    never holds a stale DB connection.

    One run failing (network error, Gemini API issue, etc.) never
    stops the loop - it's logged and retried after the next
    interval, same principle the pipeline and the management
    command's own --loop mode both already follow.
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
                name="portfolio-news-scheduler",
                daemon=True,
            )

            thread.start()

            interval_seconds = _get_interval_seconds()

            logger.info(
                "Portfolio news scheduler started. "
                "Run interval: %s seconds.",
                interval_seconds,
            )

    @classmethod
    def _run(cls):

        # Give Django time to finish startup, same as
        # MarketPriceScheduler.
        time.sleep(10)

        interval_seconds = _get_interval_seconds()

        while True:

            try:

                close_old_connections()

                stats = run_portfolio_news_monitor()

                logger.info(
                    "Portfolio news monitor run complete: "
                    "users=%s, holdings=%s, articles_matched=%s, "
                    "new_articles=%s, alerts_created=%s, "
                    "notifications_sent=%s",
                    stats['users_processed'],
                    stats['holdings_processed'],
                    stats['articles_matched'],
                    stats['articles_stored_new'],
                    stats['alerts_created'],
                    stats['notifications_sent'],
                )

            except Exception as exc:

                logger.exception(
                    "Portfolio news scheduler run failed: %s. "
                    "Will retry after the next interval.",
                    exc,
                )

            finally:

                close_old_connections()

            time.sleep(interval_seconds)
