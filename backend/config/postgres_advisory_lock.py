"""Small deployment-safe PostgreSQL advisory-lock helper.

The lock is database-backed so multiple Django worker processes do not run
the same background refresh concurrently. On SQLite/development it is a no-op.
"""
from contextlib import contextmanager
from functools import wraps
import hashlib
import logging
import threading

from django.db import connection

logger = logging.getLogger(__name__)

_LOCK_NAMESPACE = "pwms"
# PostgreSQL advisory locks are session-scoped and re-entrant. Track nesting
# within this Python thread so nested decorated commands share one acquisition
# and only the outermost context releases it.
_held_locks = threading.local()


def _held_counts():
    counts = getattr(_held_locks, "counts", None)
    if counts is None:
        counts = {}
        _held_locks.counts = counts
    return counts


def _lock_key(name):
    digest = hashlib.sha256(
        f"{_LOCK_NAMESPACE}:{name}".encode("utf-8")
    ).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=True)


@contextmanager
def postgres_advisory_lock(name):
    """Yield True when this thread acquired or already owns the shared lock."""
    if connection.vendor != "postgresql":
        yield True
        return

    counts = _held_counts()
    if counts.get(name, 0):
        counts[name] += 1
        try:
            yield True
        finally:
            counts[name] -= 1
        return

    key = _lock_key(name)
    acquired = False
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_try_advisory_lock(%s)", [key])
            acquired = bool(cursor.fetchone()[0])

        if not acquired:
            yield False
            return

        counts[name] = 1
        try:
            yield True
        finally:
            counts.pop(name, None)
    finally:
        if acquired:
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_advisory_unlock(%s)", [key])
            except Exception:
                logger.exception(
                    "Unable to explicitly release advisory lock '%s'; "
                    "the database will release it when the session closes.",
                    name,
                )


def with_postgres_advisory_lock(name):
    """Decorate a command or service entry point with a shared advisory lock."""
    def decorator(func):
        @wraps(func)
        def wrapped(*args, **kwargs):
            with postgres_advisory_lock(name) as acquired:
                if not acquired:
                    message = (
                        f"Refresh not started: advisory lock '{name}' "
                        "is held by another process."
                    )
                    logger.warning("%s (%s)", message, func.__qualname__)

                    caller = args[0] if args else None
                    stderr = getattr(caller, "stderr", None)
                    stdout = getattr(caller, "stdout", None)
                    if stderr is not None:
                        stderr.write(message)
                        # Django BaseCommand.handle() must return text or None.
                        # Preserve a structured result for non-command callers.
                        if caller.__class__.__module__.startswith(
                            "django.core.management"
                        ):
                            return None
                    elif stdout is not None:
                        stdout.write(message)

                    return {
                        "success": False,
                        "skipped": True,
                        "reason": message,
                        "lock_acquired": False,
                    }

                result = func(*args, **kwargs)
                if isinstance(result, dict):
                    result.setdefault("lock_acquired", True)
                return result

        return wrapped
    return decorator
