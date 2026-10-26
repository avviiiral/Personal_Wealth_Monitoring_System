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
        analytics = self.client.get("/api/analytics/allocation/")
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
        response = self.client.get("/api/portfolio/assets/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 0)

        self.client.force_authenticate(self.user2)
        response = self.client.get("/api/portfolio/assets/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["results"][0]["id"], asset.id)

    def test_family_id_in_request_cannot_override_active_family(self):
        # This test verifies family scoping, not Viewer write access.
        self.user1.profile.role = Role.ADMIN
        self.user1.profile.save(update_fields=["role", "updated_at"])
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
        # This test verifies family membership behavior; use a role that
        # is authorized to create financial records.
        self.user1.profile.role = Role.ADMIN
        self.user1.profile.save(update_fields=["role", "updated_at"])
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

    def test_transaction_list_does_not_leak_other_family_records(self):
        family_one_asset = self._asset(
            self.user1,
            self.family1,
            "Family One Transaction Asset",
        )
        family_two_asset = self._asset(
            self.user3,
            self.family2,
            "Family Two Transaction Asset",
        )

        family_one_transaction = Transaction.objects.create(
            owner=self.user1,
            family=self.family1,
            asset=family_one_asset,
            family_name=self.family1.name,
            portfolio="Portfolio A",
            asset_class="Equity",
            sub_class="Stocks",
            asset_name=family_one_asset.name,
            transaction_type=TransactionType.BUY,
            transaction_date="2026-01-01",
            quantity=Decimal("1"),
            price_per_unit=Decimal("100"),
            amount=Decimal("100"),
        )
        family_two_transaction = Transaction.objects.create(
            owner=self.user3,
            family=self.family2,
            asset=family_two_asset,
            family_name=self.family2.name,
            portfolio="Portfolio B",
            asset_class="Equity",
            sub_class="Stocks",
            asset_name=family_two_asset.name,
            transaction_type=TransactionType.BUY,
            transaction_date="2026-01-02",
            quantity=Decimal("1"),
            price_per_unit=Decimal("900000"),
            amount=Decimal("900000"),
        )

        self.client.force_authenticate(self.user1)
        response = self.client.get("/api/portfolio/transactions/")

        self.assertEqual(response.status_code, 200)
        rows = (
            response.data.get("results", [])
            if isinstance(response.data, dict)
            else response.data
        )
        visible_ids = {row["id"] for row in rows}
        self.assertIn(family_one_transaction.id, visible_ids)
        self.assertNotIn(family_two_transaction.id, visible_ids)
        self.assertNotIn(
            "Family Two Transaction Asset",
            str(response.data),
        )

    def test_asset_class_xirr_uses_authenticated_user_family_scope(self):
        asset = self._asset(
            self.user1,
            self.family1,
            "Family One XIRR Asset",
        )
        Holding.objects.create(
            owner=self.user1,
            family=self.family1,
            asset=asset,
            quantity=Decimal("1"),
            invested_value=Decimal("1000"),
            current_price=Decimal("1100"),
            current_value=Decimal("1100"),
            unrealized_pnl=Decimal("100"),
        )
        Transaction.objects.create(
            owner=self.user1,
            family=self.family1,
            asset=asset,
            family_name=self.family1.name,
            portfolio="Portfolio A",
            asset_class="Equity",
            sub_class="Direct Equity",
            asset_name=asset.name,
            transaction_type=TransactionType.BUY,
            transaction_date="2026-01-01",
            quantity=Decimal("1"),
            price_per_unit=Decimal("1000"),
            amount=Decimal("1000"),
        )

        other_family_asset = self._asset(
            self.user3,
            self.family2,
            "Family Two XIRR Asset",
        )
        Holding.objects.create(
            owner=self.user3,
            family=self.family2,
            asset=other_family_asset,
            quantity=Decimal("1"),
            invested_value=Decimal("900000"),
            current_price=Decimal("950000"),
            current_value=Decimal("950000"),
            unrealized_pnl=Decimal("50000"),
        )
        Transaction.objects.create(
            owner=self.user3,
            family=self.family2,
            asset=other_family_asset,
            family_name=self.family2.name,
            portfolio="Portfolio B",
            asset_class="Equity",
            sub_class="Direct Equity",
            asset_name=other_family_asset.name,
            transaction_type=TransactionType.BUY,
            transaction_date="2026-01-02",
            quantity=Decimal("1"),
            price_per_unit=Decimal("900000"),
            amount=Decimal("900000"),
        )

        self.client.force_authenticate(self.user1)
        response = self.client.get(
            "/api/analytics/wealth/performance-by-subclass/"
        )

        self.assertEqual(response.status_code, 200)
        rows = response.data["results"]
        direct_equity = next(
            row for row in rows if row["asset_class"] == "Direct Equity"
        )
        self.assertEqual(Decimal(str(direct_equity["current_value"])), Decimal("1100"))
        self.assertGreater(direct_equity["xirr"], 0)

    def test_system_owner_can_manage_families(self):
        self.client.force_authenticate(self.system_owner)
        response = self.client.post("/api/settings/groups/", {"name": "Managed Family"}, format="json")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["name"], "Managed Family")


    def test_viewer_can_read_but_cannot_create_portfolio_assets(self):
        self.client.force_authenticate(self.user1)

        read_response = self.client.get("/api/portfolio/assets/")
        self.assertEqual(read_response.status_code, 200)

        write_response = self.client.post(
            "/api/portfolio/assets/",
            {"name": "Viewer Must Not Create", "category": AssetCategory.STOCK},
            format="json",
        )
        self.assertEqual(write_response.status_code, 403)
        self.assertFalse(Asset.objects.filter(name="Viewer Must Not Create").exists())

    def test_viewer_cannot_update_or_delete_portfolio_assets(self):
        asset = self._asset(self.user1, self.family1, "Read Only Asset")
        self.client.force_authenticate(self.user1)

        update_response = self.client.patch(
            f"/api/portfolio/assets/{asset.id}/",
            {"name": "Changed by Viewer"},
            format="json",
        )
        self.assertEqual(update_response.status_code, 403)

        delete_response = self.client.delete(f"/api/portfolio/assets/{asset.id}/")
        self.assertEqual(delete_response.status_code, 403)

        asset.refresh_from_db()
        self.assertEqual(asset.name, "Read Only Asset")
        self.assertTrue(asset.is_active)

    def test_viewer_cannot_create_portfolio_transactions_or_import_transactions(self):
        asset = self._asset(self.user1, self.family1, "Transaction Permission Asset")
        self.client.force_authenticate(self.user1)

        transaction_response = self.client.post(
            "/api/portfolio/transactions/",
            {
                "asset": asset.id,
                "family_name": self.family1.name,
                "portfolio": "Permission Test",
                "asset_class": "Equity",
                "sub_class": "Stocks",
                "asset_name": asset.name,
                "transaction_type": "BUY",
                "transaction_date": "2026-01-01",
                "quantity": "1",
                "price_per_unit": "100",
                "amount": "100",
            },
            format="json",
        )
        self.assertEqual(transaction_response.status_code, 403)

        import_response = self.client.post("/api/investments/import/")
        self.assertEqual(import_response.status_code, 403)

    def test_viewer_cannot_write_manual_price(self):
        asset = self._asset(self.user1, self.family1, "Manual Price Read Only Asset")
        self.client.force_authenticate(self.user1)

        response = self.client.patch(
            f"/api/portfolio/assets/{asset.id}/manual-price/",
            {"price": "123.45"},
            format="json",
        )
        self.assertEqual(response.status_code, 403)
        from market_data.models import DataSource, MarketPrice
        self.assertFalse(
            MarketPrice.objects.filter(asset=asset, source=DataSource.MANUAL).exists()
        )

    def test_viewer_cannot_change_tax_settings(self):
        self.client.force_authenticate(self.user1)
        response = self.client.post(
            "/api/settings/tax-rates/",
            {"asset_id": 1, "tenure_months": 12, "short_term_tax_rate": 15, "long_term_tax_rate": 10},
            format="json",
        )
        self.assertEqual(response.status_code, 403)

        upload_response = self.client.post("/api/settings/tax-rates/upload/")
        self.assertEqual(upload_response.status_code, 403)

    def test_viewer_cannot_edit_mis_report_notes(self):
        self.client.force_authenticate(self.user1)
        response = self.client.patch(
            "/api/portfolio/mis-report/notes/",
            {"notes": {"sections": []}},
            format="json",
        )
        self.assertEqual(response.status_code, 403)

    def test_viewer_cannot_create_or_modify_corporate_actions(self):
        self.client.force_authenticate(self.user1)
        create_response = self.client.post(
            "/api/corporate-actions/",
            {
                "action_type": "SPLIT",
                "security_name": "Viewer Write Test",
                "ex_date": "2026-01-01",
                "old_units": "1",
                "new_units": "2",
            },
            format="json",
        )
        self.assertEqual(create_response.status_code, 403)
