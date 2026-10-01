"""Small deployment-safe PostgreSQL advisory-lock helper.

The lock is intentionally database-backed so multiple Django worker processes
do not run the same background refresh at the same time. No schema changes are
required. On SQLite/development, the helper is a no-op so existing local
behaviour is preserved.
"""

from contextlib import contextmanager
from functools import wraps
import hashlib

from django.db import connection


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
            cursor.execute(
                "SELECT pg_try_advisory_lock(%s)",
                [key],
            )
            acquired = bool(cursor.fetchone()[0])

        if not acquired:
            yield False
            return

        yield True

    finally:
        if acquired:
            try:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT pg_advisory_unlock(%s)",
                        [key],
                    )
            except Exception:
                # The database connection may already have been closed or
                # invalidated. PostgreSQL releases session locks automatically
                # when that happens, so do not mask the refresh result.
                pass


def with_postgres_advisory_lock(name):
    """Decorator for long-running refresh entry points."""

    def decorator(func):
        @wraps(func)
        def wrapped(*args, **kwargs):
            with postgres_advisory_lock(name) as acquired:
                if not acquired:
                    return None
                return func(*args, **kwargs)

        return wrapped

    return decorator
