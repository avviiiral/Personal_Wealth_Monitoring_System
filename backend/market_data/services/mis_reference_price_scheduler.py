import logging
import threading
import time

from django.db import close_old_connections

from portfolio.mis_report_service import MISReportService


logger = logging.getLogger(__name__)


def refresh_custom_reference_prices_async():
    """Start a non-blocking refresh for symbols added to MIS Notes."""
    thread = threading.Thread(
        target=_refresh_custom_reference_prices,
        name="mis-custom-reference-price-refresh",
        daemon=True,
    )
    thread.start()


def _refresh_custom_reference_prices():
    close_old_connections()
    try:
        result = MISReportService.refresh_custom_reference_prices()
        logger.info(
            "Custom MIS ticker refresh completed: references=%s refreshed=%s failed=%s records=%s",
            result.get("references", 0),
            result.get("refreshed", 0),
            result.get("failed", 0),
            result.get("records", 0),
        )
    except Exception as exc:
        logger.exception("Custom MIS ticker refresh failed: %s", exc)
    finally:
        close_old_connections()


# Yahoo Finance provides daily history for these MIS reference instruments.
# Refreshing every 30 minutes keeps the database close to the provider's latest
# value without turning the background worker into a high-frequency poller.
UPDATE_INTERVAL_SECONDS = 30 * 60


class MISReferencePriceScheduler:
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
                name="mis-reference-price-scheduler",
                daemon=True,
            )
            thread.start()
            logger.info(
                "MIS reference price scheduler started. Update interval: 30 minutes."
            )

    @classmethod
    def _run(cls):
        # Let Django finish startup before the first external-data request.
        time.sleep(20)

        while True:
            try:
                close_old_connections()
                result = MISReportService.refresh_reference_prices()
                logger.info(
                    "MIS reference price refresh completed: references=%s refreshed=%s failed=%s records=%s",
                    result.get("references", 0),
                    result.get("refreshed", 0),
                    result.get("failed", 0),
                    result.get("records", 0),
                )
            except Exception as exc:
                logger.exception("MIS reference price refresh failed: %s", exc)
            finally:
                close_old_connections()

            time.sleep(UPDATE_INTERVAL_SECONDS)
