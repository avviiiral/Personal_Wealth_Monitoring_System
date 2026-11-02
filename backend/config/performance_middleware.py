"""Low-overhead API timing and database query diagnostics."""

import logging
from time import perf_counter

from django.conf import settings
from django.db import connection


logger = logging.getLogger("pwms.performance")


class ApiPerformanceLoggingMiddleware:
    """Expose per-request timing and log slow API requests without SQL data."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not request.path.startswith("/api/"):
            return self.get_response(request)

        query_stats = {"count": 0, "duration_ms": 0.0}

        def record_query(execute, sql, params, many, context):
            started = perf_counter()
            try:
                return execute(sql, params, many, context)
            finally:
                query_stats["count"] += 1
                query_stats["duration_ms"] += (perf_counter() - started) * 1000

        started = perf_counter()
        # Capture DB time separately from total application time. SQL text and
        # parameters are deliberately never logged because they may contain PII.
        with connection.execute_wrapper(record_query):
            response = self.get_response(request)

        total_ms = (perf_counter() - started) * 1000
        db_ms = query_stats["duration_ms"]
        cache_status = response.get("X-PWMS-Cache", "BYPASS")

        response["X-PWMS-Request-Time-Ms"] = f"{total_ms:.2f}"
        response["X-PWMS-DB-Queries"] = str(query_stats["count"])
        response["X-PWMS-DB-Time-Ms"] = f"{db_ms:.2f}"
        response["Server-Timing"] = (
            f"app;dur={total_ms:.2f}, db;dur={db_ms:.2f}"
        )

        threshold_ms = max(
            0,
            getattr(settings, "API_SLOW_REQUEST_THRESHOLD_MS", 500),
        )
        if total_ms >= threshold_ms:
            logger.warning(
                "Slow API request method=%s path=%s status=%s "
                "total_ms=%.2f db_ms=%.2f db_queries=%s cache=%s",
                request.method,
                request.path,
                response.status_code,
                total_ms,
                db_ms,
                query_stats["count"],
                cache_status,
            )

        return response
