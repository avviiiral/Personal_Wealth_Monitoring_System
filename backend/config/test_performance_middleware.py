from time import sleep

from django.http import JsonResponse
from django.test import RequestFactory, SimpleTestCase, override_settings

from config.performance_middleware import ApiPerformanceLoggingMiddleware


class ApiPerformanceLoggingMiddlewareTests(SimpleTestCase):
    def test_adds_request_and_database_timing_headers(self):
        factory = RequestFactory()
        request = factory.get("/api/analytics/wealth/dashboard/")

        def get_response(_request):
            return JsonResponse({"ok": True})

        response = ApiPerformanceLoggingMiddleware(get_response)(request)

        self.assertIn("X-PWMS-Request-Time-Ms", response)
        self.assertEqual(response["X-PWMS-DB-Queries"], "0")
        self.assertEqual(response["X-PWMS-DB-Time-Ms"], "0.00")
        self.assertIn("app;dur=", response["Server-Timing"])
        self.assertIn("db;dur=", response["Server-Timing"])

    @override_settings(API_SLOW_REQUEST_THRESHOLD_MS=0)
    def test_logs_slow_api_request_without_logging_query_data(self):
        factory = RequestFactory()
        request = factory.get("/api/analytics/wealth/dashboard/?family=PrivateName")

        def get_response(_request):
            sleep(0.001)
            return JsonResponse({"ok": True})

        middleware = ApiPerformanceLoggingMiddleware(get_response)
        with self.assertLogs("pwms.performance", level="WARNING") as captured:
            middleware(request)

        log_output = " ".join(captured.output)
        self.assertIn("path=/api/analytics/wealth/dashboard/", log_output)
        self.assertNotIn("PrivateName", log_output)

    def test_skips_non_api_requests(self):
        factory = RequestFactory()
        request = factory.get("/health/")

        def get_response(_request):
            return JsonResponse({"ok": True})

        response = ApiPerformanceLoggingMiddleware(get_response)(request)
        self.assertFalse(response.has_header("X-PWMS-Request-Time-Ms"))
