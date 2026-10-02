from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APIClient

from users.models import FamilyGroup

from .models import StandardAllocation
from .services.investment_summary import InvestmentSummaryService

from decimal import Decimal 

from investments.models import (
    Asset,
    AssetCategory,
    Holding,
    Transaction,
    TransactionType,
)
from mutual_funds.models import (
    MutualFundHolding,
    MutualFundScheme,
)

class InvestmentSummaryServiceTests(TestCase):
    """
    Tests for the Dashboard Investment Summary
    (Asset Category / Asset Class breakdown).
    """

    def setUp(self):
        self.user = User.objects.create_user(
            username="investment_summary_user",
            password="testpassword123",
        )
        self.family = FamilyGroup.objects.create(name="Investment Summary Test Family", created_by=self.user)
        self.user.profile.family_groups.add(self.family)
        self.user.profile.active_family_group = self.family
        self.user.profile.save(update_fields=["active_family_group"])

    def _make_equity_holding(
        self,
        name,
        sub_class,
        current_value,
    ):
        asset = Asset.objects.create(
            owner=self.user,
            family=self.family,
            name=name,
            category=AssetCategory.STOCK,
            currency="INR",
            is_active=True,
        )

        Holding.objects.create(
            owner=self.user,
            family=self.family,
            asset=asset,
            quantity=Decimal("1"),
            average_cost=current_value,
            invested_value=current_value,
            current_price=current_value,
            current_value=current_value,
            unrealized_pnl=Decimal("0"),
        )

        Transaction.objects.create(
            owner=self.user,
            family=self.family,
            asset=asset,
            asset_class="EQUITY",
            sub_class=sub_class,
            asset_name=name,
            transaction_type=TransactionType.BUY,
            transaction_date="2026-01-01",
            quantity=Decimal("1"),
            price_per_unit=current_value,
            amount=current_value,
        )

        return asset

    def _make_mutual_fund_holding(
        self,
        name,
        category,
        current_value,
    ):
        scheme = MutualFundScheme.objects.create(
            owner=self.user,
            family=self.family,
            scheme_name=name,
            category=category,
            is_active=True,
        )

        MutualFundHolding.objects.create(
            owner=self.user,
            family=self.family,
            scheme=scheme,
            units=Decimal("1"),
            invested_value=current_value,
            average_nav=current_value,
            current_nav=current_value,
            current_value=current_value,
            unrealized_pnl=Decimal("0"),
        )

        return scheme

    def test_matches_the_specification_example(self):
        """
        Direct Equity = 40,00,000
        Equity Mutual Fund = 30,00,000
        Debt Mutual Fund = 20,00,000
        Liquid Mutual Fund = 10,00,000
        Total = 1,00,00,000

        Expected: 40% / 30% / 20% / 10%, everything else 0.
        """

        self._make_equity_holding(
            "Reliance Industries",
            "Direct Equity",
            Decimal("4000000"),
        )

        self._make_mutual_fund_holding(
            "Test Equity Fund",
            "Equity Mutual Fund",
            Decimal("3000000"),
        )

        self._make_mutual_fund_holding(
            "Test Debt Fund",
            "Debt Mutual Fund",
            Decimal("2000000"),
        )

        self._make_mutual_fund_holding(
            "Test Liquid Fund",
            "Liquid Mutual Fund",
            Decimal("1000000"),
        )

        data = InvestmentSummaryService.calculate(
            self.user
        )

        self.assertEqual(
            data["total_current_value"],
            Decimal("10000000"),
        )

        by_class = {
            row["asset_class"]: row
            for row in data["results"]
        }

        # Every configured Asset Class must be present, even at zero.
        expected_classes = {
            "Unlisted",
            "Commodity",
            "Private Equity",
            "REITs",
            "InvITs",
            "Direct Equity",
            "Equity PMS",
            "Equity AIF",
            "Equity Mutual Fund",
            "Equity LRS",
            "Debt Mutual Fund",
            "Gold Bond",
            "Liquid Mutual Fund",
            "Arbitrage Mutual Fund",
        }

        self.assertEqual(
            set(by_class.keys()),
            expected_classes,
        )

        self.assertEqual(
            by_class["Direct Equity"]["current_value"],
            Decimal("4000000"),
        )
        self.assertEqual(
            by_class["Direct Equity"]["percentage_of_total"],
            Decimal("40.00"),
        )
        self.assertEqual(
            by_class["Direct Equity"]["asset_category"],
            "Equities",
        )

        self.assertEqual(
            by_class["Equity Mutual Fund"]["percentage_of_total"],
            Decimal("30.00"),
        )
        self.assertEqual(
            by_class["Debt Mutual Fund"]["percentage_of_total"],
            Decimal("20.00"),
        )
        self.assertEqual(
            by_class["Debt Mutual Fund"]["asset_category"],
            "Fixed Income",
        )
        self.assertEqual(
            by_class["Liquid Mutual Fund"]["percentage_of_total"],
            Decimal("10.00"),
        )
        self.assertEqual(
            by_class["Liquid Mutual Fund"]["asset_category"],
            "Liquids",
        )

        # Everything else must be exactly zero, not omitted.
        for asset_class in expected_classes - {
            "Direct Equity",
            "Equity Mutual Fund",
            "Debt Mutual Fund",
            "Liquid Mutual Fund",
        }:
            self.assertEqual(
                by_class[asset_class]["current_value"],
                Decimal("0"),
            )
            self.assertEqual(
                by_class[asset_class]["percentage_of_total"],
                Decimal("0"),
            )

        # Percentages should sum to ~100%.
        total_percentage = sum(
            (
                row["percentage_of_total"]
                for row in data["results"]
            ),
            Decimal("0"),
        )

        self.assertEqual(
            total_percentage,
            Decimal("100.00"),
        )

    def test_unmapped_classification_falls_back_to_other_unlisted(self):
        """
        A holding with a classification outside the fixed master
        mapping (e.g. a direct bond, or an MF scheme imported before
        category was populated) must still be counted — under
        Other / Unlisted — never dropped.
        """

        self._make_equity_holding(
            "Some Corporate Bond",
            "Corporate Bond",
            Decimal("500000"),
        )

        self._make_mutual_fund_holding(
            "Legacy Fund With No Category",
            None,
            Decimal("500000"),
        )

        data = InvestmentSummaryService.calculate(
            self.user
        )

        by_class = {
            row["asset_class"]: row
            for row in data["results"]
        }

        self.assertEqual(
            by_class["Unlisted"]["current_value"],
            Decimal("1000000"),
        )
        self.assertEqual(
            by_class["Unlisted"]["percentage_of_total"],
            Decimal("100.00"),
        )
        self.assertEqual(
            data["total_current_value"],
            Decimal("1000000"),
        )

    def test_known_business_name_variants_are_normalized(self):
        """
        Confirms real Excel-style variants documented in
        transaction_import.py normalize to the canonical class name.
        """

        self._make_equity_holding(
            "Some AIF Strategy",
            "Equity AIF (Category III)",
            Decimal("100000"),
        )

        self._make_equity_holding(
            "Sovereign Gold Bond 2026",
            "SGB",
            Decimal("50000"),
        )

        data = InvestmentSummaryService.calculate(
            self.user
        )

        by_class = {
            row["asset_class"]: row
            for row in data["results"]
        }

        self.assertEqual(
            by_class["Equity AIF"]["current_value"],
            Decimal("100000"),
        )
        self.assertEqual(
            by_class["Gold Bond"]["current_value"],
            Decimal("50000"),
        )

    def test_empty_portfolio_returns_all_zero_rows(self):
        data = InvestmentSummaryService.calculate(
            self.user
        )

        self.assertEqual(
            data["total_current_value"],
            Decimal("0"),
        )

        self.assertEqual(
            len(data["results"]),
            14,
        )

        for row in data["results"]:
            self.assertEqual(
                row["current_value"],
                Decimal("0"),
            )
            self.assertEqual(
                row["percentage_of_total"],
                Decimal("0"),
            )



class GlobalStandardAllocationApiTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="global_allocation_user",
            password="testpassword123",
        )
        self.family_a = FamilyGroup.objects.create(
            name="Global Allocation Family A",
            created_by=self.user,
        )
        self.family_b = FamilyGroup.objects.create(
            name="Global Allocation Family B",
            created_by=self.user,
        )
        self.user.profile.family_groups.add(self.family_a, self.family_b)
        self.user.profile.active_family_group = self.family_a
        self.user.profile.save(update_fields=["active_family_group"])

        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_standard_allocation_is_shared_across_families(self):
        StandardAllocation.objects.create(
            asset_category="Equities",
            allocation_percent=Decimal("60.00"),
            allocation_amount=Decimal("600000.00"),
        )
        StandardAllocation.objects.create(
            asset_category="Fixed Income",
            allocation_percent=Decimal("40.00"),
            allocation_amount=Decimal("400000.00"),
        )

        response_a = self.client.get(
            "/api/analytics/wealth/standard-allocations/",
            {"family": self.family_a.name},
        )
        response_b = self.client.get(
            "/api/analytics/wealth/standard-allocations/",
            {"family": self.family_b.name},
        )

        self.assertEqual(response_a.status_code, 200)
        self.assertEqual(response_b.status_code, 200)
        self.assertEqual(
            response_a.data["allocations"]["Equities"]["percent"],
            60.0,
        )
        self.assertEqual(
            response_b.data["allocations"]["Equities"]["percent"],
            60.0,
        )

    def test_updating_one_family_view_updates_the_global_target(self):
        response = self.client.put(
            "/api/analytics/wealth/standard-allocations/update/",
            {
                "allocations": {
                    "Equities": {"percent": 70, "amount": 700000},
                    "Fixed Income": {"percent": 30, "amount": 300000},
                }
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200)

        family_b_response = self.client.get(
            "/api/analytics/wealth/standard-allocations/",
            {"family": self.family_b.name},
        )

        self.assertEqual(family_b_response.status_code, 200)
        self.assertEqual(
            family_b_response.data["allocations"]["Equities"]["percent"],
            70.0,
        )
        self.assertEqual(
            family_b_response.data["allocations"]["Fixed Income"]["percent"],
            30.0,
        )
