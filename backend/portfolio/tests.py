from decimal import Decimal
from datetime import date

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from investments.models import Asset, Transaction
from portfolio.services.portfolio_tree_service import (
    PortfolioTreeService,
)


class PortfolioTreeServiceTests(TestCase):

    def setUp(self):
        User = get_user_model()

        self.user = User.objects.create_user(
            username="portfolio_test_user",
            password="test-password",
        )

        self.asset = Asset.objects.create(
            owner=self.user,
            name="Test Equity",
            category="STOCK",
            isin="INE000TEST001",
        )

        Transaction.objects.create(
            owner=self.user,
            asset=self.asset,
            family_name="Family A",
            portfolio="Portfolio A",
            asset_class="Equity",
            sub_class="Large Cap",
            asset_name="Test Equity",
            transaction_date=date(2026, 1, 10),
            transaction_type="BUY",
            quantity=Decimal("10"),
            price_per_unit=Decimal("100"),
            amount=Decimal("1000"),
            fees=Decimal("0"),
        )

    def test_tree_hierarchy(self):
        result = PortfolioTreeService.build(self.user)

        self.assertEqual(result["count"], 1)

        family = result["families"][0]

        self.assertEqual(
            family["family_name"],
            "Family A",
        )

        portfolio = family["portfolios"][0]

        self.assertEqual(
            portfolio["portfolio"],
            "Portfolio A",
        )

        asset_class = portfolio["asset_classes"][0]

        self.assertEqual(
            asset_class["asset_class"],
            "Equity",
        )

        sub_class = asset_class["sub_classes"][0]

        self.assertEqual(
            sub_class["sub_class"],
            "Large Cap",
        )

        asset = sub_class["assets"][0]

        self.assertEqual(
            asset["asset_name"],
            "Test Equity",
        )

        self.assertEqual(
            asset["isin"],
            "INE000TEST001",
        )

    def test_position_calculation(self):
        result = PortfolioTreeService.build(self.user)

        asset = (
            result["families"][0]
            ["portfolios"][0]
            ["asset_classes"][0]
            ["sub_classes"][0]
            ["assets"][0]
        )

        self.assertEqual(
            asset["quantity"],
            10.0,
        )

        self.assertEqual(
            asset["invested_value"],
            1000.0,
        )

        self.assertEqual(
            asset["average_cost"],
            100.0,
        )

    def test_multiple_families_are_separated(self):
        second_asset = Asset.objects.create(
            owner=self.user,
            name="Second Equity",
            category="STOCK",
            isin="INE000TEST002",
        )

        Transaction.objects.create(
            owner=self.user,
            asset=second_asset,
            family_name="Family B",
            portfolio="Portfolio B",
            asset_class="Equity",
            sub_class="Mid Cap",
            asset_name="Second Equity",
            transaction_date=date(2026, 2, 10),
            transaction_type="BUY",
            quantity=Decimal("5"),
            price_per_unit=Decimal("200"),
            amount=Decimal("1000"),
            fees=Decimal("0"),
        )

        result = PortfolioTreeService.build(self.user)

        self.assertEqual(result["count"], 2)

        family_names = {
            family["family_name"]
            for family in result["families"]
        }

        self.assertEqual(
            family_names,
            {"Family A", "Family B"},
        )

    def test_sell_reduces_position(self):
        Transaction.objects.create(
            owner=self.user,
            asset=self.asset,
            family_name="Family A",
            portfolio="Portfolio A",
            asset_class="Equity",
            sub_class="Large Cap",
            asset_name="Test Equity",
            transaction_date=date(2026, 2, 10),
            transaction_type="SELL",
            quantity=Decimal("4"),
            price_per_unit=Decimal("120"),
            amount=Decimal("480"),
            fees=Decimal("0"),
        )

        result = PortfolioTreeService.build(self.user)

        asset = (
            result["families"][0]
            ["portfolios"][0]
            ["asset_classes"][0]
            ["sub_classes"][0]
            ["assets"][0]
        )

        self.assertEqual(
            asset["quantity"],
            6.0,
        )

        self.assertEqual(
            asset["invested_value"],
            600.0,
        )

        self.assertEqual(
            asset["average_cost"],
            100.0,
        )


class PortfolioTreeAPITests(TestCase):

    def setUp(self):
        User = get_user_model()

        self.user = User.objects.create_user(
            username="portfolio_api_user",
            password="test-password",
        )

        self.client = APIClient()

        self.client.force_authenticate(
            user=self.user
        )

        self.asset = Asset.objects.create(
            owner=self.user,
            name="API Test Equity",
            category="STOCK",
            isin="INE000TEST003",
        )

        Transaction.objects.create(
            owner=self.user,
            asset=self.asset,
            family_name="Family API",
            portfolio="Portfolio API",
            asset_class="Equity",
            sub_class="Large Cap",
            asset_name="API Test Equity",
            transaction_date=date(2026, 1, 15),
            transaction_type="BUY",
            quantity=Decimal("20"),
            price_per_unit=Decimal("50"),
            amount=Decimal("1000"),
            fees=Decimal("0"),
        )

    def test_portfolio_tree_endpoint(self):
        response = self.client.get(
            "/api/portfolio/tree/"
        )

        self.assertEqual(
            response.status_code,
            200,
        )

        data = response.json()

        self.assertTrue(data["success"])
        self.assertIn("families", data)

        family = next(
            (
                family
                for family in data["families"]
                if family["family_name"] == "Family API"
            ),
            None,
        )

        self.assertIsNotNone(family)

        portfolio = next(
            (
                portfolio
                for portfolio in family["portfolios"]
                if portfolio["portfolio"] == "Portfolio API"
            ),
            None,
        )

        self.assertIsNotNone(portfolio)

        asset_class = next(
            (
                asset_class
                for asset_class in portfolio["asset_classes"]
                if asset_class["asset_class"] == "Equity"
            ),
            None,
        )

        self.assertIsNotNone(asset_class)

        sub_class = next(
            (
                sub_class
                for sub_class in asset_class["sub_classes"]
                if sub_class["sub_class"] == "Large Cap"
            ),
            None,
        )

        self.assertIsNotNone(sub_class)

        asset = next(
            (
                asset
                for asset in sub_class["assets"]
                if asset["isin"] == "INE000TEST003"
            ),
            None,
        )

        self.assertIsNotNone(asset)

        self.assertEqual(
            asset["asset_name"],
            "API Test Equity",
        )

        self.assertEqual(
            asset["quantity"],
            20.0,
        )

        self.assertEqual(
            asset["invested_value"],
            1000.0,
        )

    def test_portfolio_tree_requires_authentication(self):
        self.client.force_authenticate(
            user=None
        )

        response = self.client.get(
            "/api/portfolio/tree/"
        )

        self.assertIn(
            response.status_code,
            [401, 403],
        )

# ======================================================================
# SHARED-VISIBILITY GROUP TESTS (multi-owner tree correctness)
#
# These specifically guard against the bug found and fixed while
# building family/group data sharing: PortfolioTreeService used to
# pass the "viewing user" into per-node metric lookups instead of
# each node's own actual owner, which silently produced missing/
# wrong XIRR for every node that didn't belong to the viewer.
# ======================================================================

from market_data.models import DataSource, MarketPrice
from users.models import FamilyGroup, Role


class PortfolioTreeMultiOwnerTests(TestCase):
    def setUp(self):
        User = get_user_model()

        self.owner_a = User.objects.create_user(
            username="tree_multi_owner_a",
            password="test-password",
        )

        self.owner_b = User.objects.create_user(
            username="tree_multi_owner_b",
            password="test-password",
        )

        group = FamilyGroup.objects.create(name="Multi Owner Test Family")

        self.owner_a.profile.family_groups.add(group)

        self.owner_b.profile.family_groups.add(group)

        self.asset_a = Asset.objects.create(
            owner=self.owner_a,
            name="Owner A Stock",
            category="STOCK",
            isin="INE000MULTIA1",
        )

        self.asset_b = Asset.objects.create(
            owner=self.owner_b,
            name="Owner B Stock",
            category="STOCK",
            isin="INE000MULTIB1",
        )

        Transaction.objects.create(
            owner=self.owner_a,
            asset=self.asset_a,
            family_name="Family A",
            portfolio="Portfolio A",
            asset_class="Equity",
            sub_class="Large Cap",
            asset_name="Owner A Stock",
            transaction_date=date(2025, 1, 10),
            transaction_type="BUY",
            quantity=Decimal("10"),
            price_per_unit=Decimal("100"),
            amount=Decimal("1000"),
            fees=Decimal("0"),
        )

        Transaction.objects.create(
            owner=self.owner_b,
            asset=self.asset_b,
            family_name="Family B",
            portfolio="Portfolio B",
            asset_class="Equity",
            sub_class="Large Cap",
            asset_name="Owner B Stock",
            transaction_date=date(2025, 2, 10),
            transaction_type="BUY",
            quantity=Decimal("5"),
            price_per_unit=Decimal("200"),
            amount=Decimal("1000"),
            fees=Decimal("0"),
        )

        MarketPrice.objects.create(
            asset=self.asset_a,
            date=date.today(),
            close_price=Decimal("150"),
            source=DataSource.MANUAL,
        )

        MarketPrice.objects.create(
            asset=self.asset_b,
            date=date.today(),
            close_price=Decimal("250"),
            source=DataSource.MANUAL,
        )

    def _find_asset_node(self, tree, isin):
        for family in tree["families"]:
            for portfolio in family["portfolios"]:
                for asset_class in portfolio["asset_classes"]:
                    for sub_class in asset_class["sub_classes"]:
                        for asset in sub_class["assets"]:
                            if asset["isin"] == isin:
                                return asset
        return None

    def test_single_owner_tree_unaffected(self):
        """Baseline: building for a single User instance (not a
        list) still works exactly as before - backward compat."""

        result = PortfolioTreeService.build(self.owner_a)

        self.assertEqual(result["count"], 1)

        node = self._find_asset_node(result, "INE000MULTIA1")

        self.assertIsNotNone(node)
        self.assertEqual(node["quantity"], 10.0)

    def test_combined_tree_includes_both_owners_assets(self):
        result = PortfolioTreeService.build([self.owner_a.id, self.owner_b.id])

        self.assertEqual(result["count"], 2)

        node_a = self._find_asset_node(result, "INE000MULTIA1")
        node_b = self._find_asset_node(result, "INE000MULTIB1")

        self.assertIsNotNone(node_a)
        self.assertIsNotNone(node_b)

    def test_combined_tree_quantities_are_correct_per_owner(self):
        result = PortfolioTreeService.build([self.owner_a.id, self.owner_b.id])

        node_a = self._find_asset_node(result, "INE000MULTIA1")
        node_b = self._find_asset_node(result, "INE000MULTIB1")

        self.assertEqual(node_a["quantity"], 10.0)
        self.assertEqual(node_a["invested_value"], 1000.0)

        self.assertEqual(node_b["quantity"], 5.0)
        self.assertEqual(node_b["invested_value"], 1000.0)

    def test_combined_tree_current_price_correct_per_owner(self):
        """Regression: current_price/current_value must reflect
        each node's own asset, not be dropped or mismatched when
        combining owners."""

        result = PortfolioTreeService.build([self.owner_a.id, self.owner_b.id])

        node_a = self._find_asset_node(result, "INE000MULTIA1")
        node_b = self._find_asset_node(result, "INE000MULTIB1")

        self.assertEqual(node_a["current_price"], 150.0)
        self.assertEqual(node_a["current_value"], 1500.0)

        self.assertEqual(node_b["current_price"], 250.0)
        self.assertEqual(node_b["current_value"], 1250.0)

    def test_combined_tree_xirr_is_computed_for_both_owners(self):
        """
        This is the direct regression test for the bug: before the
        fix, _build_asset always used the outer `owner` argument
        (whichever owner built() was originally called with as the
        "current viewer") when computing each node's XIRR. In a
        combined multi-owner tree, that meant every node NOT
        belonging to that one outer owner searched for transactions
        under the wrong owner and silently got xirr=None.

        With the fix (using each node's own real transaction owner),
        every node - regardless of which owner in the group it
        belongs to - must get a real, non-None XIRR figure.
        """

        result = PortfolioTreeService.build([self.owner_a.id, self.owner_b.id])

        node_a = self._find_asset_node(result, "INE000MULTIA1")
        node_b = self._find_asset_node(result, "INE000MULTIB1")

        self.assertIsNotNone(
            node_a["xirr"],
            "Owner A's node lost its XIRR when combined into a multi-owner tree",
        )
        self.assertIsNotNone(
            node_b["xirr"],
            "Owner B's node lost its XIRR when combined into a multi-owner tree "
            "(this is exactly the bug: it was silently computed using the wrong "
            "owner's transactions)",
        )

    def test_ungrouped_owner_only_sees_own_tree(self):
        """An owner NOT in a group must still only see their own
        data via the real view-layer flow (get_visible_owner_ids)."""

        solo = get_user_model().objects.create_user(
            username="tree_multi_owner_solo",
            password="test-password",
        )

        from users.permissions import get_visible_owner_ids

        owner_ids = get_visible_owner_ids(solo)

        self.assertEqual(owner_ids, [solo.id])

        # And the grouped pair should see each other, proven via the
        # same helper the real views call.
        grouped_ids = set(get_visible_owner_ids(self.owner_a))

        self.assertEqual(grouped_ids, {self.owner_a.id, self.owner_b.id})


class PortfolioSummaryMultiOwnerTests(TestCase):
    """Confirms the simple Sum()-based endpoints (summary, holdings)
    correctly combine group members' Holding rows - the safe,
    mechanical case (no per-item owner re-derivation needed)."""

    def setUp(self):
        User = get_user_model()

        self.owner_a = User.objects.create_user(
            username="summary_multi_owner_a",
            password="test-password",
        )

        self.owner_b = User.objects.create_user(
            username="summary_multi_owner_b",
            password="test-password",
        )

        group = FamilyGroup.objects.create(name="Summary Test Family")

        self.owner_a.profile.family_groups.add(group)

        self.owner_b.profile.family_groups.add(group)

        from investments.models import Holding

        asset_a = Asset.objects.create(
            owner=self.owner_a,
            name="Summary Owner A Stock",
            category="STOCK",
            isin="INE000SUMA001",
        )

        asset_b = Asset.objects.create(
            owner=self.owner_b,
            name="Summary Owner B Stock",
            category="STOCK",
            isin="INE000SUMB001",
        )

        Holding.objects.create(
            owner=self.owner_a,
            asset=asset_a,
            invested_value=Decimal("1000"),
            current_value=Decimal("1200"),
            unrealized_pnl=Decimal("200"),
        )

        Holding.objects.create(
            owner=self.owner_b,
            asset=asset_b,
            invested_value=Decimal("2000"),
            current_value=Decimal("1800"),
            unrealized_pnl=Decimal("-200"),
        )

    def test_portfolio_summary_combines_group_totals(self):
        client = APIClient()
        client.force_authenticate(user=self.owner_a)

        response = client.get("/api/portfolio/summary/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Decimal(str(response.data["total_invested"])), Decimal("3000"))
        self.assertEqual(Decimal(str(response.data["total_current_value"])), Decimal("3000"))
        self.assertEqual(response.data["number_of_holdings"], 2)

    def test_portfolio_holdings_includes_both_owners(self):
        client = APIClient()
        client.force_authenticate(user=self.owner_b)

        response = client.get("/api/portfolio/holdings/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 2)

    def test_solo_owner_sees_only_their_own_summary(self):
        outsider = get_user_model().objects.create_user(
            username="summary_multi_owner_outsider",
            password="test-password",
        )

        from investments.models import Holding

        outsider_asset = Asset.objects.create(
            owner=outsider,
            name="Outsider Stock",
            category="STOCK",
            isin="INE000OUTSIDE1",
        )

        Holding.objects.create(
            owner=outsider,
            asset=outsider_asset,
            invested_value=Decimal("500"),
            current_value=Decimal("500"),
            unrealized_pnl=Decimal("0"),
        )

        client = APIClient()
        client.force_authenticate(user=outsider)

        response = client.get("/api/portfolio/summary/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Decimal(str(response.data["total_invested"])), Decimal("500"))
        self.assertEqual(response.data["number_of_holdings"], 1)
