"""Regression tests for deployment configuration parsing and safe defaults.

These tests exercise configuration only; they do not import or modify financial
calculation code.
"""
import os
from unittest.mock import patch

from django.test import SimpleTestCase

from config.settings import _env_bool, _env_list


class DeploymentSecuritySettingTests(SimpleTestCase):
    def test_env_bool_parses_documented_true_values_case_insensitively(self):
        for value in ("true", "TRUE", "1", "yes", " YeS "):
            with self.subTest(value=value), patch.dict(os.environ, {"PWMS_TEST_FLAG": value}):
                self.assertTrue(_env_bool("PWMS_TEST_FLAG", False))

    def test_env_bool_treats_other_explicit_values_as_false(self):
        for value in ("false", "0", "no", "enabled", ""):
            with self.subTest(value=value), patch.dict(os.environ, {"PWMS_TEST_FLAG": value}):
                self.assertFalse(_env_bool("PWMS_TEST_FLAG", True))

    def test_env_bool_uses_default_when_variable_is_missing(self):
        with patch.dict(os.environ):
            os.environ.pop("PWMS_TEST_MISSING_FLAG", None)
            self.assertTrue(_env_bool("PWMS_TEST_MISSING_FLAG", True))
            self.assertFalse(_env_bool("PWMS_TEST_MISSING_FLAG", False))

    def test_env_list_trims_items_and_omits_empty_entries(self):
        with patch.dict(os.environ, {"PWMS_TEST_ORIGINS": " https://a.example, ,https://b.example , "}):
            self.assertEqual(
                _env_list("PWMS_TEST_ORIGINS", ["fallback"]),
                ["https://a.example", "https://b.example"],
            )

    def test_default_api_permissions_require_authentication(self):
        from django.conf import settings

        self.assertIn(
            "rest_framework.permissions.IsAuthenticated",
            settings.REST_FRAMEWORK["DEFAULT_PERMISSION_CLASSES"],
        )

    def test_configured_database_backend_matches_supported_environment_setting(self):
        from django.conf import settings

        requested = os.environ.get("DATABASE_ENGINE", "sqlite").strip().lower()
        expected = (
            "django.db.backends.postgresql"
            if requested in ("postgres", "postgresql")
            else "django.db.backends.sqlite3"
        )
        self.assertEqual(settings.DATABASES["default"]["ENGINE"], expected)
