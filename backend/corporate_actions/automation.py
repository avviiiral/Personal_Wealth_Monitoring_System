"""NSE corporate-action scans triggered outside the nightly job.

The nightly scan (03:30, once per day) is the ``corporate_actions`` job in
corporate_actions/jobs.py, run by the shared background-job framework.
Upload-triggered scans run after TransactionImporter has completed security
identity mapping and portfolio-position rebuilding.
"""
import logging
import threading
from contextlib import contextmanager

from django.core.management import call_command

logger = logging.getLogger(__name__)
_scan_lock = threading.Lock()


@contextmanager
def scan_lock():
    """Serialize scans; later triggers wait instead of being dropped."""
    _scan_lock.acquire()
    try:
        yield
    finally:
        _scan_lock.release()


def _run_nse_scan(trigger):
    """Run the existing idempotent monitor command without blocking an API request."""
    try:
        with scan_lock():
            logger.info("Starting NSE corporate-action scan (trigger=%s).", trigger)
            call_command("monitor_corporate_actions", source="nse")
            logger.info("Completed NSE corporate-action scan (trigger=%s).", trigger)
    except Exception:
        logger.exception("NSE corporate-action scan failed (trigger=%s).", trigger)


def trigger_scan_after_transaction_import(imported_count):
    """Start a scan only after successful transaction/security-master mapping."""
    if not imported_count:
        return False
    thread = threading.Thread(
        target=_run_nse_scan,
        args=("transaction_import",),
        name="pwms-corporate-actions-upload-scan",
        daemon=True,
    )
    thread.start()
    return True
