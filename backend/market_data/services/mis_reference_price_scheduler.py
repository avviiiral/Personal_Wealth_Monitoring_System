"""Non-blocking refresh for symbols added to MIS Notes.

The periodic MIS reference refresh is part of the 15-minute
``market_prices`` job (market_data/jobs.py).
"""
import logging
import threading

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
