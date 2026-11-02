"""Per-user, short-lived response caching for expensive authenticated API reads."""

import hashlib

from django.core.cache import cache
from django.http import HttpResponse
from django.utils.deprecation import MiddlewareMixin


CACHE_TTL_SECONDS = 20
CACHE_KEY_PREFIX = "pwms:api:v1"
CACHEABLE_API_PREFIXES = (
    "/api/analytics/wealth/dashboard/",
    "/api/analytics/wealth/summary/",
    "/api/analytics/wealth/allocation/",
    "/api/analytics/wealth/performance/",
    "/api/analytics/wealth/xirr/",
    "/api/analytics/wealth/investment-summary/",
    "/api/analytics/wealth/performance-by-subclass/",
    "/api/analytics/wealth/allocation-by-advisor/",
    "/api/analytics/wealth/composition-by-amc/",
    "/api/analytics/wealth/equity-analysis/",
    "/api/analytics/wealth/fixed-income-analysis/",
    "/api/analytics/wealth/sector-allocation/",
    "/api/analytics/wealth/market-cap-allocation/",
    "/api/analytics/wealth/non-stock-holding-types/",
    "/api/analytics/wealth/performance-by-advisor/",
    "/api/analytics/wealth/historical/",
    "/api/analytics/wealth/historical-period/",
    "/api/portfolio/summary/",
    "/api/portfolio/holdings/",
    "/api/portfolio/tree/",
    "/api/portfolio/holding-report/",
    "/api/portfolio/holding-matrix/",
    "/api/portfolio/equity-market-cap/",
)


class AuthenticatedApiCacheMiddleware(MiddlewareMixin):
    """Cache selected JSON GET responses without crossing user boundaries."""

    def process_request(self, request):
        user = getattr(request, "user", None)
        if (
            request.method != "GET"
            or not user
            or not user.is_authenticated
            or request.path not in CACHEABLE_API_PREFIXES
            or request.headers.get("Cache-Control", "").lower() == "no-cache"
        ):
            return None

        query = request.META.get("QUERY_STRING", "")
        material = f"{user.pk}:{request.path}?{query}"
        digest = hashlib.sha256(material.encode("utf-8")).hexdigest()
        key = f"{CACHE_KEY_PREFIX}:{digest}"
        cached = cache.get(key)
        if cached is not None:
            response = HttpResponse(
                cached["content"],
                status=cached["status"],
                content_type=cached["content_type"],
            )
            response["X-PWMS-Cache"] = "HIT"
            response["Cache-Control"] = "private, no-store"
            response["Vary"] = "Cookie"
            return response

        request._pwms_api_cache_key = key
        return None

    def process_response(self, request, response):
        key = getattr(request, "_pwms_api_cache_key", None)
        content_type = response.get("Content-Type", "")
        if (
            key
            and response.status_code == 200
            and "application/json" in content_type
            and not response.has_header("Set-Cookie")
        ):
            cache.set(
                key,
                {
                    "content": response.content,
                    "status": response.status_code,
                    "content_type": content_type,
                },
                timeout=CACHE_TTL_SECONDS,
            )
            response["X-PWMS-Cache"] = "MISS"
            response["Cache-Control"] = "private, no-store"
            response["Vary"] = "Cookie"
        return response
