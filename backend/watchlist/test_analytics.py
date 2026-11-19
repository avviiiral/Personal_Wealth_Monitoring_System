from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APIClient

from watchlist.models import (
    InvestmentProduct,
    MutualFundProduct,
    PerformanceSnapshot,
    ProductType,
)


class MutualFundAnalyticsApiTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="mf-analytics-user", password="test")
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def _create_fund(self, name, identity, category, return_1y, aum="100"):
        product = InvestmentProduct.objects.create(
            product_type=ProductType.MUTUAL_FUND,
            name=name,
            provider="Example AMC",
            category=category,
            isin=f"ISIN-{identity}",
            identity_key=f"MUTUAL_FUND:TEST:{identity}",
            source="TEST",
        )
        MutualFundProduct.objects.create(
            product=product,
            scheme_code=identity,
            plan="Direct",
            option="Growth",
            aum=Decimal(aum),
        )
        PerformanceSnapshot.objects.create(
            product=product,
            date=date(2026, 10, 28),
            return_1y=Decimal(return_1y) if return_1y is not None else None,
            source="TEST",
        )
        return product

    def test_overview_ranks_funds_and_excludes_missing_returns_from_average(self):
        leader = self._create_fund("Alpha Multi Cap", "ALPHA", "Multi Cap", "12.50", "250")
        self._create_fund("Beta Multi Cap", "BETA", "Multi Cap", "7.50", "150")
        self._create_fund("Gamma Multi Cap", "GAMMA", "Multi Cap", None, "100")
        self._create_fund("Other Category Fund", "OTHER", "Large Cap", "99.00", "500")

        response = self.client.get(
            "/api/watch-list/analytics/overview/?category=Multi%20Cap&ranking_period=1Y"
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["summary"]["fund_count"], 3)
        self.assertEqual(response.data["summary"]["funds_with_metrics"], 2)
        self.assertEqual(
            Decimal(str(response.data["summary"]["average_returns"]["1Y"]["average_return"])),
            Decimal("10.00"),
        )
        self.assertEqual(response.data["summary"]["average_returns"]["1Y"]["covered_funds"], 2)
        self.assertEqual(response.data["rankings"][0]["id"], leader.id)
        self.assertEqual(
            [item["name"] for item in response.data["rankings"]],
            ["Alpha Multi Cap", "Beta Multi Cap"],
        )
        self.assertEqual(
            Decimal(str(response.data["summary"]["aum"])),
            Decimal("500"),
        )

    def test_overview_requires_authentication(self):
        self.client.force_authenticate(user=None)
        response = self.client.get("/api/watch-list/analytics/overview/")
        self.assertEqual(response.status_code, 403)
