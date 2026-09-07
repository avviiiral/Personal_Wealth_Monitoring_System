import logging
import os
import time

from django.core.management.base import BaseCommand

from portfolio_news.services.pipeline import run_portfolio_news_monitor


logger = logging.getLogger(__name__)

DEFAULT_INTERVAL_SECONDS = 1800  # 30 minutes


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


class Command(BaseCommand):

    help = (
        "Monitor financial news for every active user's real "
        "PWMS portfolio holdings, analyze materially relevant "
        "articles with Gemini, and generate portfolio-weighted "
        "alerts. Safe to run repeatedly (idempotent). By default "
        "runs once and exits - schedule it externally (cron / "
        "Task Scheduler / systemd timer) every 30-60 minutes, or "
        "pass --loop to have this command handle its own "
        "scheduling instead, sleeping NEWS_MONITOR_INTERVAL "
        "seconds (default 1800) between runs. All other "
        "operational thresholds - lookback window, AI call "
        "pacing, per-holding article cap, and relevance/alert "
        "score floors - are configured via NEWS_MONITOR_* "
        "environment variables; see run_portfolio_news_monitor's "
        "docstring in services/pipeline.py for the full list."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--loop",
            action="store_true",
            help=(
                "Run continuously, sleeping NEWS_MONITOR_INTERVAL "
                "seconds (default 1800) between runs, instead of "
                "running once and exiting. Intended for "
                "environments without an external scheduler "
                "(e.g. a long-lived container); stop with "
                "Ctrl+C / SIGTERM."
            ),
        )

    def _run_once(self):

        self.stdout.write("Starting portfolio news monitoring run...")

        stats = run_portfolio_news_monitor()

        self.stdout.write(
            self.style.SUCCESS(
                "Portfolio news monitoring complete.\n"
                f"  Users processed:        {stats['users_processed']}\n"
                f"  Holdings processed:     {stats['holdings_processed']}\n"
                f"  Search queries run:     {stats['queries_run']}\n"
                f"  Articles retrieved:     {stats['articles_retrieved']}\n"
                f"  Provider failures:      {stats['provider_failures']}\n"
                f"  Articles matched:       {stats['articles_matched']}\n"
                f"  New articles stored:    {stats['articles_stored_new']}\n"
                f"  Duplicates skipped:     {stats['duplicates_skipped']}\n"
                f"  Articles sent to AI:    {stats['articles_sent_to_ai']}\n"
                f"  AI failures:            {stats['ai_failures']}\n"
                f"  Alerts created:         {stats['alerts_created']}\n"
                f"  Notifications sent:     {stats['notifications_sent']}"
            )
        )

    def handle(self, *args, **options):

        if not options.get("loop"):
            self._run_once()
            return

        interval_seconds = _get_interval_seconds()

        self.stdout.write(
            f"Running in loop mode (interval={interval_seconds}s). "
            "Press Ctrl+C to stop."
        )

        while True:
            try:
                self._run_once()
            except Exception:
                # A failure in one scheduled run must not kill
                # the loop - log it and try again next interval,
                # the same "one failure never aborts everything"
                # principle the pipeline itself follows.
                logger.exception(
                    "Portfolio news monitoring run failed; will "
                    "retry after the next interval."
                )

            time.sleep(interval_seconds)