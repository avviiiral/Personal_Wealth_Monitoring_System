import logging
import os
import threading
import time

from django.db import close_old_connections

from portfolio_news.services.pipeline import (
    get_monitor_interval_seconds,
    run_portfolio_news_monitor,
)
from filing_intelligence.services.pipeline import ingest_exchange_filings


logger = logging.getLogger(__name__)

DEFAULT_INTERVAL_SECONDS = 1800


def _get_interval_seconds() -> int:
    # Shared with `monitor_portfolio_news --loop`; invalid values fall
    # back to the default and tiny values are raised to a safe minimum.
    return get_monitor_interval_seconds()


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
    def _run_news_once(cls):
        stats = run_portfolio_news_monitor()

        logger.info(
            "Portfolio news monitor run complete: users=%s, holdings=%s, "
            "articles_matched=%s, new_articles=%s, alerts_created=%s, "
            "notifications_sent=%s, provider_failures=%s, "
            "query_cache_hits=%s, holding_failures=%s, user_failures=%s",
            stats["users_processed"],
            stats["holdings_processed"],
            stats["articles_matched"],
            stats["articles_stored_new"],
            stats["alerts_created"],
            stats["notifications_sent"],
            stats["provider_failures"],
            stats["query_cache_hits"],
            stats["holding_failures"],
            stats["user_failures"],
        )

    @classmethod
    def _run_filings_once(cls):
        if os.environ.get("FILING_INTELLIGENCE_ENABLED", "false").lower() not in (
            "1",
            "true",
            "yes",
        ):
            return

        filing_stats = ingest_exchange_filings()
        logger.info("Exchange filing monitor complete: %s", filing_stats)

    @classmethod
    def _run(cls):
        time.sleep(10)

        while True:
            # The news pass and the filing pass are isolated from each
            # other: a failure in one must not skip the other, and neither
            # may stop the loop.
            try:
                close_old_connections()
                cls._run_news_once()
            except Exception as exc:
                logger.exception(
                    "Portfolio news scheduler run failed: %s. Will retry after the next interval.",
                    exc,
                )
            finally:
                close_old_connections()

            try:
                close_old_connections()
                cls._run_filings_once()
            except Exception as exc:
                logger.exception(
                    "Exchange filing scheduler run failed: %s. Will retry after the next interval.",
                    exc,
                )
            finally:
                close_old_connections()

            # Re-read each cycle so the interval is always valid.
            time.sleep(_get_interval_seconds())
