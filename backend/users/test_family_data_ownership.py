from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APIClient
from rest_framework.exceptions import PermissionDenied

from investments.services.transaction_import import TransactionImporter

from investments.models import Asset, AssetCategory, Holding, Transaction, TransactionType, TransactionEditHistory
from users.models import FamilyGroup, Role


class FamilyScopedFinancialDataTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.family1 = FamilyGroup.objects.create(name="Family 1")
        self.family2 = FamilyGroup.objects.create(name="Family 2")

        self.user1 = User.objects.create_user(username="user1", password="pass123")
        self.user2 = User.objects.create_user(username="user2", password="pass123")
        self.user3 = User.objects.create_user(username="user3", password="pass123")
        self.system_owner = User.objects.create_user(username="owner", password="pass123")
        self.system_owner.profile.role = Role.SYSTEM_OWNER
        self.system_owner.is_superuser = True
        self.system_owner.save(update_fields=["is_superuser"])
        self.system_owner.profile.role = Role.SYSTEM_OWNER
        self.system_owner.profile.save(update_fields=["role", "updated_at"])

        for user, family in ((self.user1, self.family1), (self.user2, self.family1), (self.user3, self.family2)):
            user.profile.role = Role.VIEWER
            user.profile.family_groups.add(family)
            user.profile.active_family_group = family
            user.profile.save(update_fields=["active_family_group", "updated_at"])

    def _asset(self, owner, family, name="Shared Asset"):
        return Asset.objects.create(
            owner=owner,
            family=family,
            name=name,
            category=AssetCategory.STOCK,
            asset_class="Equity",
            sub_class="Stocks",
            is_active=True,
        )

    def test_same_family_member_sees_uploaded_data(self):
        asset = self._asset(self.user1, self.family1)
        self.client.force_authenticate(self.user2)
        response = self.client.get("/api/portfolio/assets/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual({row["id"] for row in response.data["results"]}, {asset.id})

    def test_same_family_member_can_access_portfolio_and_analytics(self):
        asset = self._asset(self.user1, self.family1)
        Holding.objects.create(
            owner=self.user1,
            family=self.family1,
            asset=asset,
            quantity=Decimal("10"),
            invested_value=Decimal("1000"),
            current_price=Decimal("120"),
            current_value=Decimal("1200"),
            unrealized_pnl=Decimal("200"),
        )
        self.client.force_authenticate(self.user2)
        holdings = self.client.get("/api/portfolio/holdings/")
        self.assertEqual(holdings.status_code, 200)
        self.assertEqual(holdings.data["count"], 1)
        summary = self.client.get("/api/analytics/summary/")
        self.assertEqual(summary.status_code, 200)
        analytics = self.client.get("/api/analytics/portfolio/")
        self.assertEqual(analytics.status_code, 200)

    def test_user_deletion_preserves_family_data_and_nulls_uploader(self):
        asset = self._asset(self.user1, self.family1)
        tx = Transaction.objects.create(
            owner=self.user1,
            family=self.family1,
            asset=asset,
            family_name="Family 1",
            portfolio="Portfolio A",
            asset_class="Equity",
            sub_class="Stocks",
            asset_name=asset.name,
            transaction_type=TransactionType.BUY,
            transaction_date="2026-01-01",
            quantity=Decimal("1"),
            price_per_unit=Decimal("100"),
            amount=Decimal("100"),
        )
        history = TransactionEditHistory.objects.create(
            transaction=tx,
            owner=self.user1,
            family=self.family1,
            edited_by=self.user1,
            old_values={},
            new_values={},
            changed_fields=[],
        )
        user1_id = self.user1.id
        self.user1.delete()

        asset.refresh_from_db()
        tx.refresh_from_db()
        history.refresh_from_db()
        self.assertEqual(asset.family_id, self.family1.id)
        self.assertIsNone(asset.owner_id)
        self.assertEqual(tx.family_id, self.family1.id)
        self.assertIsNone(tx.owner_id)
        self.assertIsNone(history.owner_id)
        self.assertIsNone(history.edited_by_id)

        self.client.force_authenticate(self.user2)
        response = self.client.get("/api/portfolio/assets/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["results"][0]["id"], asset.id)
        self.assertNotEqual(user1_id, self.user2.id)
        self.assertEqual(self.family1.assets.filter(pk=asset.id).count(), 1)

    def test_removed_member_loses_access_but_data_remains(self):
        asset = self._asset(self.user1, self.family1)
        self.user1.profile.family_groups.remove(self.family1)
        self.user1.profile.active_family_group = None
        self.user1.profile.save(update_fields=["active_family_group", "updated_at"])

        self.client.force_authenticate(self.user1)
        self.assertEqual(self.client.get("/api/portfolio/assets/").status_code, 403)

        self.client.force_authenticate(self.user2)
        response = self.client.get("/api/portfolio/assets/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["results"][0]["id"], asset.id)

    def test_family_id_in_request_cannot_override_active_family(self):
        self.client.force_authenticate(self.user1)
        response = self.client.post("/api/portfolio/assets/", {
            "name": "Family 1 Asset",
            "category": AssetCategory.STOCK,
            "family": self.family2.id,
        }, format="json")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["family"], self.family1.id)

    def test_no_family_cannot_create_financial_data_or_import(self):
        user = User.objects.create_user(username="orphan", password="pass123")
        self.client.force_authenticate(user)
        response = self.client.post("/api/portfolio/assets/", {
            "name": "Orphan Asset",
            "category": AssetCategory.STOCK,
        }, format="json")
        self.assertEqual(response.status_code, 403)

    def test_family_two_is_isolated_from_family_one(self):
        asset1 = self._asset(self.user1, self.family1, "Family 1 Asset")
        asset2 = self._asset(self.user3, self.family2, "Family 2 Asset")

        self.client.force_authenticate(self.user3)
        response = self.client.get(f"/api/portfolio/assets/{asset1.id}/")
        self.assertEqual(response.status_code, 404)

        self.client.force_authenticate(self.user2)
        response = self.client.get(f"/api/portfolio/assets/{asset2.id}/")
        self.assertEqual(response.status_code, 404)

    def test_user_can_join_family_two_and_create_family_two_data(self):
        self.user1.profile.family_groups.add(self.family2)
        self.user1.profile.active_family_group = self.family2
        self.user1.profile.save(update_fields=["active_family_group", "updated_at"])

        self.client.force_authenticate(self.user1)
        response = self.client.post("/api/portfolio/assets/", {
            "name": "Family 2 Asset",
            "category": AssetCategory.STOCK,
        }, format="json")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data.get("family"), self.family2.id)

        self.client.force_authenticate(self.user2)
        response = self.client.get(f"/api/portfolio/assets/{response.data['id']}/")
        self.assertEqual(response.status_code, 404)

    def test_system_owner_can_manage_families(self):
        self.client.force_authenticate(self.system_owner)
        response = self.client.post("/api/settings/groups/", {"name": "Managed Family"}, format="json")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["name"], "Managed Family")
