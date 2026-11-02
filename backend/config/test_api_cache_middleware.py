from django.core.cache import cache
from django.test import RequestFactory, SimpleTestCase, override_settings
from django.contrib.auth import get_user_model

from config.middleware import AuthenticatedApiCacheMiddleware


@override_settings(
    CACHES={
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "pwms-cache-middleware-tests",
        }
    }
)
class AuthenticatedApiCacheMiddlewareTests(SimpleTestCase):
    def setUp(self):
        cache.clear()
        self.factory = RequestFactory()
        self.calls = 0

    def _response(self, request):
        self.calls += 1
        from django.http import JsonResponse
        return JsonResponse({"call": self.calls})

    def _middleware(self):
        return AuthenticatedApiCacheMiddleware(self._response)

    def _request(self, user, path="/api/analytics/wealth/dashboard/", query=""):
        request = self.factory.get(path + (f"?{query}" if query else ""))
        request.user = user
        return request

    def _user(self, user_id):
        User = get_user_model()
        return User(pk=user_id, username=f"cache-user-{user_id}")

    def test_reuses_response_for_same_user_and_query(self):
        middleware = self._middleware()
        user = self._user(101)

        first = middleware(self._request(user))
        second = middleware(self._request(user))

        self.assertEqual(first["X-PWMS-Cache"], "MISS")
        self.assertEqual(second["X-PWMS-Cache"], "HIT")
        self.assertEqual(self.calls, 1)

    def test_never_shares_cached_response_between_users(self):
        middleware = self._middleware()

        first = middleware(self._request(self._user(101)))
        second = middleware(self._request(self._user(202)))

        self.assertEqual(first["X-PWMS-Cache"], "MISS")
        self.assertEqual(second["X-PWMS-Cache"], "MISS")
        self.assertEqual(self.calls, 2)

    def test_query_string_is_part_of_cache_key(self):
        middleware = self._middleware()
        user = self._user(101)

        middleware(self._request(user, query="period=30d"))
        result = middleware(self._request(user, query="period=90d"))

        self.assertEqual(result["X-PWMS-Cache"], "MISS")
        self.assertEqual(self.calls, 2)

    def test_mutating_requests_are_not_cached(self):
        middleware = self._middleware()
        user = self._user(101)
        request = self.factory.post("/api/analytics/wealth/recalculate/")
        request.user = user

        first = middleware(request)
        second = middleware(request)

        self.assertFalse(first.has_header("X-PWMS-Cache"))
        self.assertFalse(second.has_header("X-PWMS-Cache"))
        self.assertEqual(self.calls, 2)

    def test_unselected_endpoint_is_not_cached(self):
        middleware = self._middleware()
        user = self._user(101)

        middleware(self._request(user, path="/api/market-data/stocks/search/"))
        result = middleware(self._request(user, path="/api/market-data/stocks/search/"))

        self.assertFalse(result.has_header("X-PWMS-Cache"))
        self.assertEqual(self.calls, 2)

    def test_anonymous_request_is_not_cached(self):
        middleware = self._middleware()
        request = self.factory.get("/api/analytics/wealth/dashboard/")
        from django.contrib.auth.models import AnonymousUser
        request.user = AnonymousUser()

        middleware(request)
        result = middleware(request)

        self.assertFalse(result.has_header("X-PWMS-Cache"))
        self.assertEqual(self.calls, 2)

    def test_no_cache_request_bypasses_cached_response(self):
        middleware = self._middleware()
        user = self._user(101)
        middleware(self._request(user))

        request = self._request(user)
        request.headers = {"Cache-Control": "no-cache"}
        result = middleware(request)

        self.assertFalse(result.has_header("X-PWMS-Cache"))
        self.assertEqual(self.calls, 2)
