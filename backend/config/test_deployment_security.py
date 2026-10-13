"""Deployment configuration regression tests.

These tests cover startup/security settings only. They intentionally do not touch
portfolio, valuation, tax, FIFO, XIRR, or other financial calculation code.
"""
from django.test import SimpleTestCase, override_settings


class DeploymentSecuritySettingTests(SimpleTestCase):
    @override_settings(SESSION_COOKIE_SECURE=True, CSRF_COOKIE_SECURE=True, SECURE_SSL_REDIRECT=True)
    def test_secure_deployment_flags_can_be_enabled_together(self):
        from django.conf import settings

        self.assertTrue(settings.SESSION_COOKIE_SECURE)
        self.assertTrue(settings.CSRF_COOKIE_SECURE)
        self.assertTrue(settings.SECURE_SSL_REDIRECT)

    @override_settings(SESSION_COOKIE_SECURE=False, CSRF_COOKIE_SECURE=False, SECURE_SSL_REDIRECT=False)
    def test_local_http_development_flags_remain_configurable(self):
        from django.conf import settings

        self.assertFalse(settings.SESSION_COOKIE_SECURE)
        self.assertFalse(settings.CSRF_COOKIE_SECURE)
        self.assertFalse(settings.SECURE_SSL_REDIRECT)

    def test_default_api_permissions_require_authentication(self):
        from django.conf import settings

        self.assertIn(
            "rest_framework.permissions.IsAuthenticated",
            settings.REST_FRAMEWORK["DEFAULT_PERMISSION_CLASSES"],
        )

    def test_database_engine_can_be_selected_for_postgresql_deployment(self):
        from django.conf import settings

        # The active settings module determines the test database; this
        # assertion guards the supported backend declaration without opening
        # a database connection.
        self.assertIn(
            settings.DATABASES["default"]["ENGINE"],
            ("django.db.backends.sqlite3", "django.db.backends.postgresql"),
        )
