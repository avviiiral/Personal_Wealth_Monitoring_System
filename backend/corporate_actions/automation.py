"""Development-server automation for NSE corporate-action monitoring.

The daily scheduler is attached only to Django's runserver process. Production
deployments should use an external process scheduler rather than an in-process
thread. Upload-triggered scans run after TransactionImporter has completed
security identity mapping and portfolio-position rebuilding.
"""
import logging
import os
import sys
import threading
import time
from datetime import datetime, time as datetime_time, timedelta

from django.conf import settings
from django.core.management import call_command
from django.utils import timezone

from config.pwms_config import get as get_pwms_config

logger = logging.getLogger(__name__)
_scan_lock = threading.Lock()
_scheduler_started = False
_scheduler_guard = threading.Lock()


def _run_nse_scan(trigger):
    """Run the existing idempotent monitor command without blocking an API request."""
    # Serialize scans, but queue later triggers instead of dropping them.
    # This matters when a transaction upload completes during the daily scan.
    _scan_lock.acquire()
    try:
        logger.info("Starting NSE corporate-action scan (trigger=%s).", trigger)
        call_command("monitor_corporate_actions", source="nse")
        logger.info("Completed NSE corporate-action scan (trigger=%s).", trigger)
    except Exception:
        logger.exception("NSE corporate-action scan failed (trigger=%s).", trigger)
    finally:
        _scan_lock.release()


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


def _next_daily_run(now, run_time):
    target = datetime.combine(now.date(), run_time, tzinfo=now.tzinfo)
    if target <= now:
        target += timedelta(days=1)
    return target


def _daily_scheduler():
    configured_time = get_pwms_config("corporate_actions", "daily_run_time", "06:00")
    try:
        run_time = datetime_time.fromisoformat(str(configured_time))
    except ValueError:
        logger.error("Invalid corporate_actions.daily_run_time=%r; scheduler disabled.", configured_time)
        return

    now = timezone.localtime()
    logger.info(
        "Daily NSE corporate-action scheduler started; configured run time is %s local time.",
        configured_time,
    )
    # If the backend starts at or after 06:00, perform today's scan immediately
    # instead of silently postponing it until tomorrow.
    if now.time().replace(tzinfo=None) >= run_time:
        _run_nse_scan("server_start_catchup")

    while True:
        now = timezone.localtime()
        target = _next_daily_run(now, run_time)
        wait_seconds = max(1, (target - now).total_seconds())
        time.sleep(wait_seconds)
        _run_nse_scan("daily_06_00")


def start_daily_scheduler():
    """Start one daily scheduler thread in the Django development server."""
    global _scheduler_started
    if not get_pwms_config("corporate_actions", "daily_scheduler_enabled", True):
        return False
    if len(sys.argv) < 2 or sys.argv[1] != "runserver":
        return False
    # Django's autoreloader starts a parent and a serving child. Only the child
    # may own the scheduler, otherwise every scan would run twice.
    if os.environ.get("RUN_MAIN") != "true" and "--noreload" not in sys.argv:
        return False
    with _scheduler_guard:
        if _scheduler_started:
            return False
        thread = threading.Thread(
            target=_daily_scheduler,
            name="pwms-corporate-actions-daily-scheduler",
            daemon=True,
        )
        thread.start()
        _scheduler_started = True
    return True
