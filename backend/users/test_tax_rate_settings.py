from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APIClient

from users.models import TaxRateSetting


class TaxRateSettingsApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user1 = User.objects.create_user(username="tax-user-1", password="pass123")
        self.user2 = User.objects.create_user(username="tax-user-2", password="pass123")

    def test_user_can_create_list_update_and_delete_tax_rate(self):
        self.client.force_authenticate(self.user1)

        create = self.client.post(
            "/api/settings/tax-rates/",
            {"asset_name": "HDFC Bank", "tax_rate": "15.50"},
            format="json",
        )
        self.assertEqual(create.status_code, 201)
        self.assertEqual(create.data["asset_name"], "HDFC Bank")
        self.assertEqual(create.data["tax_rate"], "15.50")

        listing = self.client.get("/api/settings/tax-rates/")
        self.assertEqual(listing.status_code, 200)
        self.assertEqual(len(listing.data), 1)

        tax_id = create.data["id"]
        update = self.client.patch(
            f"/api/settings/tax-rates/{tax_id}/",
            {"asset_name": "HDFC Bank", "tax_rate": "20"},
            format="json",
        )
        self.assertEqual(update.status_code, 200)
        self.assertEqual(update.data["tax_rate"], "20")

        delete = self.client.delete(f"/api/settings/tax-rates/{tax_id}/")
        self.assertEqual(delete.status_code, 200)
        self.assertFalse(TaxRateSetting.objects.filter(pk=tax_id).exists())

    def test_tax_rate_is_scoped_to_authenticated_user(self):
        row = TaxRateSetting.objects.create(
            user=self.user1,
            asset_name="HDFC Bank",
            tax_rate=Decimal("15"),
        )

        self.client.force_authenticate(self.user2)
        listing = self.client.get("/api/settings/tax-rates/")
        self.assertEqual(listing.status_code, 200)
        self.assertEqual(listing.data, [])

        detail = self.client.patch(
            f"/api/settings/tax-rates/{row.id}/",
            {"tax_rate": "20"},
            format="json",
        )
        self.assertEqual(detail.status_code, 404)

    def test_tax_rate_must_be_between_zero_and_one_hundred(self):
        self.client.force_authenticate(self.user1)

        for value in ("-1", "100.01", "not-a-number"):
            response = self.client.post(
                "/api/settings/tax-rates/",
                {"asset_name": "HDFC Bank", "tax_rate": value},
                format="json",
            )
            self.assertEqual(response.status_code, 400)
