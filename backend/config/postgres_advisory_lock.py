"""Small deployment-safe PostgreSQL advisory-lock helper.

The lock is database-backed so multiple Django worker processes do not run
same background refresh concurrently. On SQLite/development it is a no-op.
"""
from contextlib import contextmanager
from functools import wraps
import hashlib
import logging

from django.db import connection

logger = logging.getLogger(__name__)

_LOCK_NAMESPACE = "pwms"


def _lock_key(name):
    digest = hashlib.sha256(
        f"{_LOCK_NAMESPACE}:{name}".encode("utf-8")
    ).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=True)


@contextmanager
def postgres_advisory_lock(name):
    """Yield True when the current process acquired the shared lock."""
    if connection.vendor != "postgresql":
        yield True
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

        yield True
    finally:
        if acquired:
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_advisory_unlock(%s)", [key])
            except Exception:
                # PostgreSQL releases session locks when the connection closes.
                # Do not mask a completed refresh if that connection is invalid.
                logger.exception(
                    "Unable to explicitly release PostgreSQL advisory lock %s; "
                    "the database will release it when the session closes.",
                    name,
                )


def with_postgres_advisory_lock(name):
    """Decorator for refresh entry points with explicit contention reporting."""
    def decorator(func):
        @wraps(func)
        def wrapped(*args, **kwargs):
            with postgres_advisory_lock(name) as acquired:
                if not acquired:
                    logger.warning(
                        "Refresh entry point %s was not run because advisory "
                        "lock '%s' is already held by another process.",
                        func.__qualname__,
                        name,
                    )
                    message = (
                        f"Refresh not started: advisory lock '{name}' "
                        "is held by another process."
                    )
                    command_stdout = getattr(args[0], "stdout", None) if args else None
                    command_stderr = getattr(args[0], "stderr", None) if args else None
                    if command_stderr is not None:
                        command_stderr.write(message)
                    elif command_stdout is not None:
                        command_stdout.write(message)
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
