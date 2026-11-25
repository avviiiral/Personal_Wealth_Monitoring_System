"""Windows-safe file helpers shared by the scheduler and the Security Master.

On Windows ``os.replace`` fails with ``PermissionError`` (WinError 5 "Access
is denied" or WinError 32 "being used by another process") while another
process holds the target open: Excel, an antivirus scanner, or another PWMS
thread reading the same workbook. Writes therefore go to a temporary file in
the same directory and are moved into place with a few short retries. A
failed write never leaves a half-written target behind.
"""
import logging
import os
import tempfile
import time
from pathlib import Path

from django.conf import settings

logger = logging.getLogger(__name__)

DEFAULT_RETRIES = 5
DEFAULT_RETRY_DELAY_SECONDS = 0.5


def data_dir():
    """Directory for PWMS runtime data (workbook, scheduler state, reports).

    ``settings.PWMS_DATA_DIR`` wins when set; tests point it at a temporary
    directory so they can never write into the real ``backend/data``.
    """
    configured = getattr(settings, "PWMS_DATA_DIR", None)
    if configured:
        return Path(configured)
    return Path(settings.BASE_DIR) / "data"


def reports_dir():
    return data_dir() / "reports"


def replace_with_retry(source, target, *, retries=DEFAULT_RETRIES, retry_delay=DEFAULT_RETRY_DELAY_SECONDS):
    """``os.replace`` with retries for transient Windows sharing violations."""
    attempts = max(1, int(retries))
    for attempt in range(1, attempts + 1):
        try:
            os.replace(source, target)
            return
        except PermissionError:
            if attempt >= attempts:
                raise
            time.sleep(retry_delay * attempt)


def atomic_write_bytes(path, payload, *, retries=DEFAULT_RETRIES, retry_delay=DEFAULT_RETRY_DELAY_SECONDS):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(prefix=f".{target.stem}.", suffix=".tmp", dir=str(target.parent))
    temp_path = Path(temp_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        replace_with_retry(temp_path, target, retries=retries, retry_delay=retry_delay)
    except BaseException:
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            logger.debug("Could not remove temporary file %s", temp_path, exc_info=True)
        raise


def atomic_write_text(path, text, *, retries=DEFAULT_RETRIES, retry_delay=DEFAULT_RETRY_DELAY_SECONDS):
    atomic_write_bytes(path, text.encode("utf-8"), retries=retries, retry_delay=retry_delay)
