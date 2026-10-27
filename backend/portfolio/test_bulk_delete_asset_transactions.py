from datetime import date
from decimal import Decimal
from unittest.mock import call, patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from investments.models import Asset, Transaction, TransactionEditHistory
from users.models import FamilyGroup, Role


class PortfolioBulkDeleteAssetTransactionsTests(TestCase):
    def setUp(self):
        self.family_a = FamilyGroup.objects.create(name="Bulk Delete Family A")
        self.family_b = FamilyGroup.objects.create(name="Bulk Delete Family B")
        self.super_user = self._make_user(
            "bulk_delete_super_user", Role.SUPER_USER, self.family_a
        )
        self.asset_a = self._make_asset(
            self.family_a, self.super_user, "Test Equity"
        )
        self.transaction_a = self._make_transaction(
            self.family_a, self.super_user, self.asset_a, "Test Equity"
        )
        self.client = APIClient()

        self.holding_rebuild = patch(
            "portfolio.views.HoldingCalculationEngine.rebuild_holding"
        ).start()
        self.position_rebuild = patch(
            "portfolio.views.PortfolioPositionEngine.rebuild_all_for_family"
        ).start()
        self.addCleanup(patch.stopall)

    def _make_user(self, username, role, family):
        User = get_user_model()
        user = User.objects.create_user(username=username, password="test-password")
        profile = user.profile
        profile.role = role
        profile.family_groups.add(family)
        profile.active_family_group = family
        profile.save()
        return user

    def _make_asset(self, family, owner, name):
        return Asset.objects.create(
            family=family,
            owner=owner,
            name=name,
            category="STOCK",
            isin="",
        )

    def _make_transaction(self, family, owner, asset, displayed_name):
        return Transaction.objects.create(
            owner=owner,
            family=family,
            asset=asset,
            family_name="Family Member",
            portfolio="Portfolio A",
            asset_class="Equity",
            sub_class="Large Cap",
            asset_name=displayed_name,
            transaction_type="BUY",
            transaction_date=date(2026, 1, 10),
            quantity=Decimal("10"),
            price_per_unit=Decimal("100"),
            amount=Decimal("1000"),
            fees=Decimal("0"),
        )

    def _delete(self, user, asset_name="Test Equity"):
        self.client.force_authenticate(user=user)
        return self.client.post(
            "/api/portfolio/transactions/delete-by-asset-name/",
            {"asset_name": asset_name},
            format="json",
        )

    def test_viewer_and_admin_cannot_bulk_delete_transactions(self):
        for index, role in enumerate((Role.VIEWER, Role.ADMIN)):
            actor = self._make_user(
                "bulk_delete_blocked_" + str(index), role, self.family_a
            )
            response = self._delete(actor)
            self.assertEqual(response.status_code, 403)

        self.assertTrue(Transaction.objects.filter(pk=self.transaction_a.pk).exists())

    def test_super_user_deletes_only_matching_transactions_in_active_family(self):
        family_b_user = self._make_user(
            "bulk_delete_family_b_user", Role.VIEWER, self.family_b
        )
        asset_b = self._make_asset(self.family_b, family_b_user, "Test Equity")
        transaction_b = self._make_transaction(
            self.family_b, family_b_user, asset_b, ""
        )

        response = self._delete(self.super_user)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["deleted_count"], 1)
        self.assertFalse(Transaction.objects.filter(pk=self.transaction_a.pk).exists())
        self.assertTrue(Transaction.objects.filter(pk=transaction_b.pk).exists())
        self.assertTrue(Asset.objects.filter(pk=self.asset_a.pk).exists())
        self.assertEqual(
            TransactionEditHistory.objects.filter(edited_by=self.super_user).count(),
            1,
        )
        self.holding_rebuild.assert_called_once_with(self.asset_a)
        self.position_rebuild.assert_called_once_with(self.family_a)

    def test_system_owner_deletes_matching_transactions_across_families(self):
        family_b_user = self._make_user(
            "bulk_delete_system_owner_family_b_user", Role.VIEWER, self.family_b
        )
        asset_b = self._make_asset(self.family_b, family_b_user, "Test Equity")
        transaction_b = self._make_transaction(
            self.family_b, family_b_user, asset_b, ""
        )
        system_owner = self._make_user(
            "bulk_delete_system_owner", Role.SYSTEM_OWNER, self.family_a
        )

        response = self._delete(system_owner)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["deleted_count"], 2)
        self.assertFalse(
            Transaction.objects.filter(pk__in=[self.transaction_a.pk, transaction_b.pk]).exists()
        )
        self.assertEqual(
            TransactionEditHistory.objects.filter(edited_by=system_owner).count(),
            2,
        )
        self.assertEqual(self.holding_rebuild.call_count, 2)
        self.position_rebuild.assert_has_calls(
            [call(self.family_a), call(self.family_b)],
            any_order=True,
        )

    def test_asset_name_is_required(self):
        response = self._delete(self.super_user, "   ")
        self.assertEqual(response.status_code, 400)
        self.assertTrue(Transaction.objects.filter(pk=self.transaction_a.pk).exists())
