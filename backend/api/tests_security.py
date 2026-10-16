from django.core.cache import cache
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from django.contrib.auth import get_user_model
from users.models import Role


User = get_user_model()


class AuthenticationSecurityTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(
            username="security_viewer",
            password="Strong-test-password-123!",
            email="security_viewer@example.com",
        )
        self.user.profile.role = Role.VIEWER
        self.user.profile.save(update_fields=["role"])

    def tearDown(self):
        cache.clear()

    def test_login_requires_csrf_token(self):
        client = APIClient(enforce_csrf_checks=True)

        response = client.post(
            "/api/auth/login/",
            {
                "username": self.user.username,
                "password": "Strong-test-password-123!",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 403)

    def test_login_succeeds_with_csrf_token(self):
        client = APIClient(enforce_csrf_checks=True)

        health = client.get("/api/health/")
        self.assertEqual(health.status_code, 200)

        csrf_token = client.cookies["csrftoken"].value

        response = client.post(
            "/api/auth/login/",
            {
                "username": self.user.username,
                "password": "Strong-test-password-123!",
            },
            format="json",
            HTTP_X_CSRFTOKEN=csrf_token,
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["authenticated"])

    @override_settings(
        REST_FRAMEWORK={
            "DEFAULT_PERMISSION_CLASSES": [
                "rest_framework.permissions.IsAuthenticated"
            ],
            "DEFAULT_AUTHENTICATION_CLASSES": [
                "rest_framework.authentication.SessionAuthentication"
            ],
            "DEFAULT_THROTTLE_CLASSES": [
                "rest_framework.throttling.ScopedRateThrottle"
            ],
            "DEFAULT_THROTTLE_RATES": {
                "login": "2/minute",
            },
        }
    )
    def test_login_is_rate_limited(self):
        client = APIClient(enforce_csrf_checks=True)
        client.get("/api/health/")
        csrf_token = client.cookies["csrftoken"].value

        payload = {
            "username": self.user.username,
            "password": "wrong-password",
        }

        for _ in range(2):
            response = client.post(
                "/api/auth/login/",
                payload,
                format="json",
                HTTP_X_CSRFTOKEN=csrf_token,
            )
            self.assertEqual(response.status_code, 401)

        response = client.post(
            "/api/auth/login/",
            payload,
            format="json",
            HTTP_X_CSRFTOKEN=csrf_token,
        )

        self.assertEqual(response.status_code, 429)
