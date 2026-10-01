from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APIClient

from investments.models import Asset, AssetCategory
from users.models import FamilyGroup, TaxRateChangeLog, TaxRateSetting


class TaxRateSettingsApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user1 = User.objects.create_user(username="tax-user-1", password="pass123")
        self.user2 = User.objects.create_user(username="tax-user-2", password="pass123")
        self.family = FamilyGroup.objects.create(name="Tax Family")
        self.user1.profile.family_groups.add(self.family)
        self.user2.profile.family_groups.add(self.family)
        self.user1.profile.active_family_group = self.family
        self.user1.profile.save(update_fields=["active_family_group"])
        self.user2.profile.active_family_group = self.family
        self.user2.profile.save(update_fields=["active_family_group"])

        self.asset1 = Asset.objects.create(
            owner=self.user1,
            family=self.family,
            name="HDFC Bank",
            category=AssetCategory.STOCK,
        )
        self.asset2 = Asset.objects.create(
            owner=self.user1,
            family=self.family,
            name="Nippon India Growth",
            category=AssetCategory.MUTUAL_FUND,
        )

    def test_all_family_assets_are_listed_before_configuration(self):
        self.client.force_authenticate(self.user1)

        listing = self.client.get("/api/settings/tax-rates/")

        self.assertEqual(listing.status_code, 200)
        self.assertEqual([row["asset_name"] for row in listing.data], [
            "HDFC Bank",
            "Nippon India Growth",
        ])
        self.assertIsNone(listing.data[0]["id"])
        self.assertIsNone(listing.data[0]["tenure_months"])

    def test_user_can_save_update_and_clear_family_tax_settings(self):
        self.client.force_authenticate(self.user1)

        create = self.client.post(
            "/api/settings/tax-rates/",
            {
                "asset_id": self.asset1.id,
                "tenure_months": 12,
                "short_term_tax_rate": "20.00",
                "long_term_tax_rate": "10.00",
            },
            format="json",
        )

        self.assertEqual(create.status_code, 201)
        self.assertEqual(create.data["asset_name"], "HDFC Bank")
        self.assertEqual(create.data["family_name"], "Tax Family")
        self.assertEqual(create.data["tenure_months"], 12)
        self.assertEqual(create.data["short_term_tax_rate"], "20.00")
        self.assertEqual(create.data["long_term_tax_rate"], "10.00")

        first_log = TaxRateChangeLog.objects.get()
        self.assertEqual(first_log.username, self.user1.username)
        self.assertEqual(first_log.asset_name, "HDFC Bank")
        self.assertEqual(first_log.change_from["tenure_months"], None)
        self.assertEqual(first_log.change_to["tenure_months"], 12)
        self.assertEqual(first_log.change_from["short_term_tax_rate"], None)
        self.assertEqual(first_log.change_to["short_term_tax_rate"], "20.00")

        tax_id = create.data["id"]

        update = self.client.patch(
            f"/api/settings/tax-rates/{tax_id}/",
            {
                "tenure_months": 24,
                "short_term_tax_rate": "15.50",
                "long_term_tax_rate": "8.50",
            },
            format="json",
        )

        self.assertEqual(update.status_code, 200)
        self.assertEqual(update.data["tenure_months"], 24)
        self.assertEqual(update.data["short_term_tax_rate"], "15.50")
        self.assertEqual(update.data["long_term_tax_rate"], "8.50")

        logs = TaxRateChangeLog.objects.filter(asset=self.asset1).order_by("id")
        self.assertEqual(logs.count(), 2)
        self.assertEqual(logs[1].change_from["tenure_months"], 12)
        self.assertEqual(logs[1].change_to["tenure_months"], 24)
        self.assertEqual(logs[1].change_from["long_term_tax_rate"], "10.00")
        self.assertEqual(logs[1].change_to["long_term_tax_rate"], "8.50")

        self.client.force_authenticate(self.user2)
        listing = self.client.get("/api/settings/tax-rates/")
        self.assertEqual(listing.status_code, 200)
        configured = next(row for row in listing.data if row["asset_id"] == self.asset1.id)
        self.assertEqual(configured["tenure_months"], 24)
        self.assertEqual(configured["long_term_tax_rate"], "8.50")

        self.client.force_authenticate(self.user1)
        delete = self.client.delete(f"/api/settings/tax-rates/{tax_id}/")
        self.assertEqual(delete.status_code, 200)
        self.assertFalse(TaxRateSetting.objects.filter(pk=tax_id).exists())

        logs = TaxRateChangeLog.objects.filter(asset=self.asset1).order_by("id")
        self.assertEqual(logs.count(), 3)
        self.assertEqual(logs[2].change_from["tenure_months"], 24)
        self.assertEqual(logs[2].change_to["tenure_months"], None)

    def test_tax_update_history_returns_user_datetime_asset_and_changes(self):
        self.client.force_authenticate(self.user1)

        response = self.client.post(
            "/api/settings/tax-rates/",
            {
                "asset_id": self.asset1.id,
                "tenure_months": 12,
                "short_term_tax_rate": "20",
                "long_term_tax_rate": "10",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201)

        history = self.client.get("/api/settings/tax-rates/history/")
        self.assertEqual(history.status_code, 200)
        self.assertEqual(len(history.data), 1)
        self.assertEqual(history.data[0]["user"], self.user1.username)
        self.assertEqual(history.data[0]["asset_name"], "HDFC Bank")
        self.assertIn("date_time", history.data[0])
        self.assertEqual(history.data[0]["change_from"]["tenure_months"], None)
        self.assertEqual(history.data[0]["change_to"]["tenure_months"], 12)


    def test_duplicate_asset_names_are_returned_once_and_share_settings(self):
        duplicate = Asset.objects.create(
            owner=self.user1,
            family=self.family,
            name="HDFC Bank",
            category=AssetCategory.STOCK,
        )

        self.client.force_authenticate(self.user1)
        listing = self.client.get("/api/settings/tax-rates/")

        self.assertEqual(listing.status_code, 200)
        hdfc_rows = [row for row in listing.data if row["asset_name"] == "HDFC Bank"]
        self.assertEqual(len(hdfc_rows), 1)

        response = self.client.post(
            "/api/settings/tax-rates/",
            {
                "asset_id": duplicate.id,
                "tenure_months": 12,
                "short_term_tax_rate": "20",
                "long_term_tax_rate": "10",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(
            TaxRateSetting.objects.filter(family=self.family, asset__name="HDFC Bank").count(),
            1,
        )

        listing = self.client.get("/api/settings/tax-rates/")
        hdfc_rows = [row for row in listing.data if row["asset_name"] == "HDFC Bank"]
        self.assertEqual(len(hdfc_rows), 1)
        self.assertEqual(hdfc_rows[0]["tenure_months"], 12)


    def test_tax_settings_cannot_target_an_asset_from_another_family(self):
        other_family = FamilyGroup.objects.create(name="Other Family")
        other_asset = Asset.objects.create(
            owner=self.user2,
            family=other_family,
            name="Other Asset",
            category=AssetCategory.STOCK,
        )

        self.client.force_authenticate(self.user1)
        response = self.client.post(
            "/api/settings/tax-rates/",
            {
                "asset_id": other_asset.id,
                "tenure_months": 12,
                "short_term_tax_rate": "20",
                "long_term_tax_rate": "10",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)

    def test_tax_rates_and_tenure_are_validated(self):
        self.client.force_authenticate(self.user1)

        invalid_payloads = [
            {
                "asset_id": self.asset1.id,
                "tenure_months": -1,
                "short_term_tax_rate": "20",
                "long_term_tax_rate": "10",
            },
            {
                "asset_id": self.asset1.id,
                "tenure_months": 12,
                "short_term_tax_rate": "-1",
                "long_term_tax_rate": "10",
            },
            {
                "asset_id": self.asset1.id,
                "tenure_months": 12,
                "short_term_tax_rate": "20",
                "long_term_tax_rate": "100.01",
            },
            {
                "asset_id": self.asset1.id,
                "tenure_months": 12,
                "short_term_tax_rate": "not-a-number",
                "long_term_tax_rate": "10",
            },
        ]

        for payload in invalid_payloads:
            response = self.client.post(
                "/api/settings/tax-rates/",
                payload,
                format="json",
            )
            self.assertEqual(response.status_code, 400)

    def test_tax_setting_is_family_shared(self):
        TaxRateSetting.objects.create(
            family=self.family,
            asset=self.asset1,
            tenure_months=36,
            short_term_tax_rate=Decimal("17.5"),
            long_term_tax_rate=Decimal("7.5"),
        )

        self.client.force_authenticate(self.user2)
        listing = self.client.get("/api/settings/tax-rates/")

        self.assertEqual(listing.status_code, 200)
        configured = next(row for row in listing.data if row["asset_id"] == self.asset1.id)
        self.assertEqual(configured["tenure_months"], 36)
        self.assertEqual(configured["short_term_tax_rate"], "17.50")
        self.assertEqual(configured["long_term_tax_rate"], "7.50")
