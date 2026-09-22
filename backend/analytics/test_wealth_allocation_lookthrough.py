from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase

from users.models import FamilyGroup
from rest_framework.test import APIClient

from investments.models import Asset, AssetCategory, Holding
from mutual_funds.models import MutualFundHolding, MutualFundScheme, MutualFundUnderlying


class WealthAllocationLookThroughApiTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="wealth-allocation-user", password="pw")
        self.family = FamilyGroup.objects.create(name="Wealth Allocation Test Family", created_by=self.user)
        self.user.profile.family_groups.add(self.family)
        self.user.profile.active_family_group = self.family
        self.user.profile.save(update_fields=["active_family_group"])
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def test_wealth_allocation_replaces_mutual_fund_with_underlying_exposure(self):
        equity_asset = Asset.objects.create(
            owner=self.user,
            family=self.family,
            name="Direct Equity",
            category=AssetCategory.STOCK,
            currency="INR",
            is_active=True,
        )
        Holding.objects.create(
            owner=self.user,
            family=self.family,
            asset=equity_asset,
            quantity=Decimal("1"),
            average_cost=Decimal("40000"),
            invested_value=Decimal("40000"),
            current_price=Decimal("40000"),
            current_value=Decimal("40000"),
            unrealized_pnl=Decimal("0"),
        )

        scheme = MutualFundScheme.objects.create(
            owner=self.user,
            family=self.family,
            scheme_name="Equity Fund",
            scheme_code="MF-1",
            is_active=True,
        )
        MutualFundHolding.objects.create(
            owner=self.user,
            family=self.family,
            scheme=scheme,
            units=Decimal("100"),
            invested_value=Decimal("60000"),
            average_nav=Decimal("600"),
            current_nav=Decimal("600"),
            current_value=Decimal("60000"),
            unrealized_pnl=Decimal("0"),
        )
        MutualFundUnderlying.objects.create(
            scheme=scheme,
            security_name="Underlying Stock",
            isin="INF000000001",
            security_key="INF000000001",
            percentage_of_nav=Decimal("100"),
            sector="Financial Services",
            portfolio_date="2026-09-15",
            source="AMFI",
        )

        response = self.client.get("/api/analytics/wealth/allocation/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["results"], [
            {
                "category": "STOCK",
                "value": Decimal("100000"),
                "percentage": Decimal("100.00"),
            },
        ])
        self.assertNotIn("MUTUAL_FUND", {row["category"] for row in response.data["results"]})
