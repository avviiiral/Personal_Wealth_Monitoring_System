from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from datetime import date
from rest_framework.test import APIClient

from investments.models import Asset, AssetCategory, Transaction, TransactionType
from users.models import FamilyGroup, Role, TaxRateChangeLog, TaxRateSetting


class TaxRateSettingsApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user1 = User.objects.create_user(username="tax-user-1", password="pass123")
        self.user2 = User.objects.create_user(username="tax-user-2", password="pass123")
        # These API tests exercise tax-setting behavior as an authorized writer.
        self.user1.profile.role = Role.ADMIN
        self.user1.profile.save(update_fields=["role"])
        self.user2.profile.role = Role.ADMIN
        self.user2.profile.save(update_fields=["role"])
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

        Transaction.objects.create(
            owner=self.user1,
            family=self.family,
            family_name="Test",
            portfolio="Test Portfolio",
            asset_class="EQUITY",
            sub_class="Direct Equity",
            asset_name="Family Portfolio - HDFC Bank",
            underlying="HDFC Bank",
            advisors="Test Advisor",
            asset=self.asset1,
            transaction_type=TransactionType.BUY,
            transaction_date=date(2026, 1, 1),
            quantity=Decimal("10"),
            price_per_unit=Decimal("100"),
            amount=Decimal("1000"),
            source="MANUAL",
        )
        Transaction.objects.create(
            owner=self.user1,
            family=self.family,
            family_name="Test",
            portfolio="Test Portfolio",
            asset_class="MUTUAL FUND",
            sub_class="Mutual Fund",
            asset_name="Nippon India Growth - Direct Growth",
            underlying="Nippon India Growth",
            advisors="Test Advisor",
            asset=self.asset2,
            transaction_type=TransactionType.BUY,
            transaction_date=date(2026, 1, 1),
            quantity=Decimal("10"),
            price_per_unit=Decimal("100"),
            amount=Decimal("1000"),
            source="MANUAL",
        )

    def test_all_family_assets_are_listed_before_configuration(self):
        self.client.force_authenticate(self.user1)

        listing = self.client.get("/api/settings/tax-rates/")

        self.assertEqual(listing.status_code, 200)
        self.assertEqual([row["asset_name"] for row in listing.data], [
            "Family Portfolio - HDFC Bank",
            "Nippon India Growth - Direct Growth",
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
        self.assertEqual(create.data["asset_name"], "Family Portfolio - HDFC Bank")
        self.assertEqual(create.data["family_name"], "Tax Family")
        self.assertEqual(create.data["tenure_months"], 12)
        self.assertEqual(create.data["short_term_tax_rate"], "20.00")
        self.assertEqual(create.data["long_term_tax_rate"], "10.00")

        first_log = TaxRateChangeLog.objects.get()
        self.assertEqual(first_log.username, self.user1.username)
        self.assertEqual(first_log.asset_name, "Family Portfolio - HDFC Bank")
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
        self.assertEqual(logs[1].change_from["long_term_tax_rate"], "10.0000")
        self.assertEqual(logs[1].change_to["long_term_tax_rate"], "8.50")

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
        self.assertEqual(history.data[0]["asset_name"], "Family Portfolio - HDFC Bank")
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
        hdfc_rows = [row for row in listing.data if row["asset_name"] == "Family Portfolio - HDFC Bank"]
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
            TaxRateSetting.objects.filter(family__isnull=True, asset__isnull=True, asset_name="Family Portfolio - HDFC Bank").count(),
            1,
        )

        listing = self.client.get("/api/settings/tax-rates/")
        hdfc_rows = [row for row in listing.data if row["asset_name"] == "Family Portfolio - HDFC Bank"]
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

    def test_post_resolves_the_submitted_display_name_for_shared_underlying(self):
        Transaction.objects.create(
            owner=self.user1,
            family=self.family,
            family_name="Test",
            portfolio="Second Portfolio",
            asset_class="EQUITY",
            sub_class="Direct Equity",
            asset_name="Another Display Label",
            underlying="HDFC Bank",
            advisors="Test Advisor",
            asset=self.asset1,
            transaction_type=TransactionType.BUY,
            transaction_date=date(2026, 2, 1),
            quantity=Decimal("2"),
            price_per_unit=Decimal("150"),
            amount=Decimal("300"),
            source="MANUAL",
        )
        self.client.force_authenticate(self.user1)

        response = self.client.post(
            "/api/settings/tax-rates/",
            {
                "asset_id": self.asset1.id,
                "asset_name": "Another Display Label",
                "tenure_months": 24,
                "short_term_tax_rate": "18",
                "long_term_tax_rate": "9",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["asset_name"], "Another Display Label")
        self.assertEqual(response.data["asset_id"], self.asset1.id)
        self.assertTrue(
            TaxRateSetting.objects.filter(
                family__isnull=True,
                asset__isnull=True,
                asset_name="Another Display Label",
            ).exists()
        )
        self.assertFalse(
            TaxRateSetting.objects.filter(
                family__isnull=True,
                asset__isnull=True,
                asset_name="Family Portfolio - HDFC Bank",
            ).exists()
        )
        history = self.client.get("/api/settings/tax-rates/history/")
        self.assertEqual(history.status_code, 200)
        self.assertEqual(history.data[0]["asset_name"], "Another Display Label")

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

    def test_global_tax_setting_is_shared_with_family_members(self):
        TaxRateSetting.objects.create(
            asset_name="Family Portfolio - HDFC Bank",
            family=None,
            asset=None,
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

    def test_global_policy_is_visible_and_editable_across_distinct_families(self):
        self.client.force_authenticate(self.user1)
        create = self.client.post(
            "/api/settings/tax-rates/",
            {
                "asset_id": self.asset1.id,
                "tenure_months": 12,
                "short_term_tax_rate": "20",
                "long_term_tax_rate": "10",
            },
            format="json",
        )
        self.assertEqual(create.status_code, 201)
        global_id = create.data["id"]

        other_user = User.objects.create_user(username="tax-user-other-family", password="pass123")
        other_family = FamilyGroup.objects.create(name="Other Tax Family")
        other_user.profile.family_groups.add(other_family)
        other_user.profile.active_family_group = other_family
        other_user.profile.save(update_fields=["active_family_group"])
        other_asset = Asset.objects.create(
            owner=other_user,
            family=other_family,
            name="HDFC Internal Position B",
            category=AssetCategory.STOCK,
        )
        Transaction.objects.create(
            owner=other_user,
            family=other_family,
            family_name="Other Family Portfolio",
            portfolio="Other Portfolio",
            asset_class="EQUITY",
            sub_class="Direct Equity",
            asset_name="Family Portfolio - HDFC Bank",
            underlying="HDFC Internal Position B",
            advisors="Other Advisor",
            asset=other_asset,
            transaction_type=TransactionType.BUY,
            transaction_date=date(2026, 1, 1),
            quantity=Decimal("4"),
            price_per_unit=Decimal("200"),
            amount=Decimal("800"),
            source="MANUAL",
        )

        self.client.force_authenticate(other_user)
        listing = self.client.get("/api/settings/tax-rates/")
        self.assertEqual(listing.status_code, 200)
        configured = next(
            row for row in listing.data
            if row["asset_name"] == "Family Portfolio - HDFC Bank"
        )
        self.assertEqual(configured["id"], global_id)
        self.assertEqual(configured["family_id"], other_family.id)
        self.assertEqual(configured["tenure_months"], 12)
        self.assertFalse(configured["configuration_conflict"])

        update = self.client.patch(
            f"/api/settings/tax-rates/{global_id}/",
            {"tenure_months": 24, "short_term_tax_rate": "16", "long_term_tax_rate": "8"},
            format="json",
        )
        self.assertEqual(update.status_code, 200)
        self.assertEqual(update.data["tenure_months"], 24)

        self.client.force_authenticate(self.user1)
        listing = self.client.get("/api/settings/tax-rates/")
        configured = next(row for row in listing.data if row["asset_name"] == "Family Portfolio - HDFC Bank")
        self.assertEqual(configured["tenure_months"], 24)
        self.assertEqual(configured["short_term_tax_rate"], "16.0000")

        self.client.force_authenticate(other_user)
        history = self.client.get("/api/settings/tax-rates/history/")
        self.assertEqual(history.status_code, 200)
        self.assertEqual(len(history.data), 2)
        self.assertEqual(
            {entry["user"] for entry in history.data},
            {"tax-user-1", "tax-user-other-family"},
        )

    def test_conflicting_legacy_policies_are_flagged_not_selected(self):
        second_asset = Asset.objects.create(
            owner=self.user1,
            family=self.family,
            name="Second HDFC Internal Position",
            category=AssetCategory.STOCK,
        )
        Transaction.objects.create(
            owner=self.user1,
            family=self.family,
            family_name="Test",
            portfolio="Test Portfolio",
            asset_class="EQUITY",
            sub_class="Direct Equity",
            asset_name="Family Portfolio - HDFC Bank",
            underlying="Second HDFC Internal Position",
            advisors="Test Advisor",
            asset=second_asset,
            transaction_type=TransactionType.BUY,
            transaction_date=date(2026, 2, 1),
            quantity=Decimal("2"),
            price_per_unit=Decimal("100"),
            amount=Decimal("200"),
            source="MANUAL",
        )
        for asset, short_rate, long_rate in (
            (self.asset1, Decimal("20"), Decimal("10")),
            (second_asset, Decimal("25"), Decimal("12")),
        ):
            TaxRateSetting.objects.create(
                family=self.family,
                asset=asset,
                asset_name="Family Portfolio - HDFC Bank",
                tenure_months=12,
                short_term_tax_rate=short_rate,
                long_term_tax_rate=long_rate,
                needs_review=True,
                review_reason="Conflicting legacy policies require manual review.",
            )

        self.client.force_authenticate(self.user1)
        listing = self.client.get("/api/settings/tax-rates/")
        configured = next(
            row for row in listing.data
            if row["asset_name"] == "Family Portfolio - HDFC Bank"
        )
        self.assertIsNone(configured["id"])
        self.assertIsNone(configured["short_term_tax_rate"])
        self.assertTrue(configured["configuration_conflict"])
        self.assertEqual(
            TaxRateSetting.objects.filter(
                family=self.family,
                asset_name="Family Portfolio - HDFC Bank",
                needs_review=True,
            ).count(),
            2,
        )

        resolve = self.client.post(
            "/api/settings/tax-rates/",
            {
                "asset_id": self.asset1.id,
                "asset_name": "Family Portfolio - HDFC Bank",
                "tenure_months": 24,
                "short_term_tax_rate": "18",
                "long_term_tax_rate": "9",
            },
            format="json",
        )
        self.assertEqual(resolve.status_code, 201)
        self.assertFalse(resolve.data["configuration_conflict"])
        legacy_rows = TaxRateSetting.objects.filter(
            family=self.family,
            asset_name="Family Portfolio - HDFC Bank",
        ).order_by("asset_id")
        self.assertEqual(legacy_rows.count(), 2)
        self.assertFalse(legacy_rows.filter(needs_review=True).exists())
        self.assertEqual(
            list(legacy_rows.values_list("short_term_tax_rate", flat=True)),
            [Decimal("20.0000"), Decimal("25.0000")],
        )
