from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase

from users.models import FamilyGroup

from analytics.services.mutual_fund_lookthrough import MutualFundLookThroughService
from investments.models import Asset, AssetCategory, Holding, SecurityMaster
from mutual_funds.models import MutualFundHolding, MutualFundScheme, MutualFundUnderlying


class MutualFundLookThroughTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="analytics-mf-test", password="test")
        self.family = FamilyGroup.objects.create(name="Analytics MF Test Family", created_by=self.user)
        self.user.profile.family_groups.add(self.family)
        self.user.profile.active_family_group = self.family
        self.user.profile.save(update_fields=["active_family_group"])

        self.stock = Asset.objects.create(
            owner=self.user,
            family=self.family,
            name="HDFC Bank Limited",
            category=AssetCategory.STOCK,
            isin="INE040A01034",
            is_active=True,
        )
        self.stock.security_master = SecurityMaster.objects.create(
            owner=self.user,
            isin="INE040A01034",
            asset_name="HDFC Bank Limited",
            sector="Banks",
        )
        self.stock.save(update_fields=["security_master"])
        Holding.objects.create(
            owner=self.user,
            asset=self.stock,
            quantity=Decimal("10"),
            invested_value=Decimal("4000"),
            current_price=Decimal("500"),
            current_value=Decimal("5000"),
            unrealized_pnl=Decimal("1000"),
        )

        self.scheme = MutualFundScheme.objects.create(
            owner=self.user,
            scheme_name="Test Equity Fund",
            scheme_code="888888",
            isin_growth="INF000000002",
        )
        MutualFundHolding.objects.create(
            owner=self.user,
            scheme=self.scheme,
            units=Decimal("1000"),
            invested_value=Decimal("9000"),
            current_nav=Decimal("10"),
            current_value=Decimal("10000"),
        )
        MutualFundUnderlying.objects.create(
            scheme=self.scheme,
            security_name="HDFC Bank Limited",
            isin="INE040A01034",
            security_key="ISIN:INE040A01034",
            quantity=Decimal("100"),
            market_value=Decimal("1000"),
            percentage_of_nav=Decimal("8.2000"),
            sector="Banks",
            portfolio_date=date(2026, 8, 31),
            source="AMFI",
        )

    def test_allocation_lookthrough_does_not_double_count(self):
        direct = list(Holding.objects.filter(owner=self.user).select_related("asset"))
        results = MutualFundLookThroughService.allocation(self.user, direct)
        total = sum((row["value"] for row in results), Decimal("0"))
        self.assertEqual(total, Decimal("15000"))

        # The direct HDFC Bank holding (₹5,000) and the mutual-fund
        # look-through exposure (₹820) are intentionally aggregated into the
        # same STOCK bucket: ₹5,820 total stock exposure.
        stock = next(row for row in results if row["category"] == "STOCK")
        self.assertEqual(stock["value"], Decimal("5820"))
        self.assertFalse(any(row["category"] == "MUTUAL_FUND" for row in results))

    def test_sector_allocation_includes_mutual_fund_exposure(self):
        direct = list(Holding.objects.filter(owner=self.user).select_related("asset__security_master"))
        result = MutualFundLookThroughService.sector_allocation(
            self.user,
            direct,
            {self.stock.id},
        )
        banks = next(row for row in result["results"] if row["sector"] == "Banks")
        self.assertEqual(banks["current_value"], Decimal("5820"))
