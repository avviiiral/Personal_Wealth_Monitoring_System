from django.contrib.auth.models import User
from rest_framework import status
from rest_framework.test import APITestCase


class WealthAnalyticsAPITests(APITestCase):
    """
    Regression tests for the unified wealth analytics APIs.

    These tests verify:

    - Authentication
    - Endpoint availability
    - Response structure
    - Historical date range handling
    """

    def setUp(self):
        self.user = User.objects.create_user(
            username="api_test_user",
            password="testpassword123",
        )

        self.client.force_authenticate(
            user=self.user
        )

    # ==========================================================
    # SUMMARY
    # ==========================================================

    def test_wealth_summary(self):
        response = self.client.get(
            "/api/analytics/wealth/summary/"
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_200_OK,
        )

        data = response.json()

        self.assertIn(
            "total_invested",
            data,
        )

        self.assertIn(
            "total_current_value",
            data,
        )

        self.assertIn(
            "realized_pnl",
            data,
        )

        self.assertIn(
            "unrealized_pnl",
            data,
        )

        self.assertIn(
            "total_pnl",
            data,
        )

        self.assertIn(
            "return_percentage",
            data,
        )

        self.assertIn(
            "xirr_percentage",
            data,
        )

        self.assertIn(
            "equity",
            data,
        )

        self.assertIn(
            "mutual_funds",
            data,
        )

    # ==========================================================
    # ALLOCATION
    # ==========================================================

    def test_wealth_allocation(self):
        response = self.client.get(
            "/api/analytics/wealth/allocation/"
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_200_OK,
        )

        data = response.json()

        self.assertIn(
            "results",
            data,
        )

        self.assertIsInstance(
            data["results"],
            list,
        )

    # ==========================================================
    # PERFORMANCE
    # ==========================================================

    def test_wealth_performance(self):
        response = self.client.get(
            "/api/analytics/wealth/performance/"
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_200_OK,
        )

        data = response.json()

        self.assertIn(
            "results",
            data,
        )

        self.assertIsInstance(
            data["results"],
            list,
        )

    # ==========================================================
    # XIRR
    # ==========================================================

    def test_wealth_xirr(self):
        response = self.client.get(
            "/api/analytics/wealth/xirr/"
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_200_OK,
        )

        data = response.json()

        self.assertIn(
            "xirr_percentage",
            data,
        )

    # ==========================================================
    # HISTORICAL
    # ==========================================================

    def test_wealth_historical_default(self):
        response = self.client.get(
            "/api/analytics/wealth/historical/"
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_200_OK,
        )

        data = response.json()

        self.assertEqual(
            data["days"],
            30,
        )

        self.assertEqual(
            len(data["results"]),
            30,
        )

        self.assertIn(
            "date",
            data["results"][0],
        )

        self.assertIn(
            "invested_value",
            data["results"][0],
        )

        self.assertIn(
            "portfolio_value",
            data["results"][0],
        )

        self.assertIn(
            "pnl",
            data["results"][0],
        )

    def test_wealth_historical_custom_range(self):
        response = self.client.get(
            "/api/analytics/wealth/historical/?days=7"
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_200_OK,
        )

        data = response.json()

        self.assertEqual(
            data["days"],
            7,
        )

        self.assertEqual(
            len(data["results"]),
            7,
        )

    def test_wealth_historical_maximum_range(self):
        response = self.client.get(
            "/api/analytics/wealth/historical/?days=3650"
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_200_OK,
        )

        data = response.json()

        self.assertEqual(
            data["days"],
            3650,
        )

        self.assertEqual(
            len(data["results"]),
            3650,
        )

    # ==========================================================
    # INVALID RANGE
    # ==========================================================

    def test_wealth_historical_zero_days_is_clamped(self):
        response = self.client.get(
            "/api/analytics/wealth/historical/?days=0"
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_200_OK,
        )

        data = response.json()

        self.assertEqual(
            data["days"],
            1,
        )

        self.assertEqual(
            len(data["results"]),
            1,
        )

    def test_wealth_historical_invalid_days_is_defaulted(self):
        response = self.client.get(
            "/api/analytics/wealth/historical/?days=invalid"
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_200_OK,
        )

        data = response.json()

        self.assertEqual(
            data["days"],
            30,
        )

    # ==========================================================
    # AUTHENTICATION
    # ==========================================================

    def test_wealth_summary_requires_authentication(self):
        self.client.force_authenticate(
            user=None
        )

        response = self.client.get(
            "/api/analytics/wealth/summary/"
        )

        self.assertIn(
            response.status_code,
            (
                status.HTTP_401_UNAUTHORIZED,
                status.HTTP_403_FORBIDDEN,
            ),
        )

    def test_wealth_historical_requires_authentication(self):
        self.client.force_authenticate(
            user=None
        )

        response = self.client.get(
            "/api/analytics/wealth/historical/"
        )

        self.assertIn(
            response.status_code,
            (
                status.HTTP_401_UNAUTHORIZED,
                status.HTTP_403_FORBIDDEN,
            ),
        )