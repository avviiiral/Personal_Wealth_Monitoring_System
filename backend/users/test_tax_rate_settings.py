from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APIClient

from investments.models import Asset, AssetCategory
from users.models import FamilyGroup, TaxRateSetting


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
        self.assertEqual(create.data["short_term_tax_rate"], "20.0000")
        self.assertEqual(create.data["long_term_tax_rate"], "10.0000")

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
        self.assertEqual(update.data["short_term_tax_rate"], "15.5000")
        self.assertEqual(update.data["long_term_tax_rate"], "8.5000")

        self.client.force_authenticate(self.user2)
        listing = self.client.get("/api/settings/tax-rates/")
        self.assertEqual(listing.status_code, 200)
        configured = next(row for row in listing.data if row["asset_id"] == self.asset1.id)
        self.assertEqual(configured["tenure_months"], 24)
        self.assertEqual(configured["long_term_tax_rate"], "8.5000")

        self.client.force_authenticate(self.user1)
        delete = self.client.delete(f"/api/settings/tax-rates/{tax_id}/")
        self.assertEqual(delete.status_code, 200)
        self.assertFalse(TaxRateSetting.objects.filter(pk=tax_id).exists())

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
        self.assertEqual(configured["short_term_tax_rate"], "17.5000")
        self.assertEqual(configured["long_term_tax_rate"], "7.5000")
