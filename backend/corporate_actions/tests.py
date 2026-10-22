from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APIClient

from .models import CorporateAction, CorporateActionStatus, CorporateActionType
from .services import quantity_multiplier, resolve_corporate_action_candidates


class CorporateActionServiceTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="corp-action-test", password="test-password")

    def make_action(self, **overrides):
        values = {
            "owner": self.user,
            "action_type": CorporateActionType.STOCK_SPLIT,
            "security_name": "Example Limited",
            "isin": "INE000A01000",
            "symbol": "EXAMPLE",
            "exchange": "NSE",
            "ex_date": date(2025, 6, 15),
            "old_units": Decimal("1"),
            "new_units": Decimal("2"),
            "status": CorporateActionStatus.CONFIRMED,
        }
        values.update(overrides)
        return CorporateAction.objects.create(**values)

    def test_confirmed_split_multiplier_only_applies_after_transaction_date(self):
        self.make_action()
        self.assertEqual(
            quantity_multiplier(owner=self.user, isin="INE000A01000",
                                transaction_date=date(2025, 1, 1), valuation_date=date(2025, 7, 1)),
            Decimal("2"),
        )
        self.assertEqual(
            quantity_multiplier(owner=self.user, isin="INE000A01000",
                                transaction_date=date(2025, 7, 1), valuation_date=date(2025, 8, 1)),
            Decimal("1"),
        )

    def test_pending_action_does_not_adjust_quantity(self):
        self.make_action(status=CorporateActionStatus.PENDING)
        self.assertEqual(
            quantity_multiplier(owner=self.user, isin="INE000A01000",
                                transaction_date=date(2025, 1, 1), valuation_date=date(2025, 7, 1)),
            Decimal("1"),
        )

    def test_exact_isin_match_prevents_name_based_guessing(self):
        self.make_action()
        self.assertEqual(resolve_corporate_action_candidates(
            owner=self.user, isin="INE999Z99999", symbol="EXAMPLE"
        ).count(), 0)

    def test_user_cannot_read_another_users_action(self):
        action = self.make_action()
        other = User.objects.create_user(username="corp-action-other", password="test-password")
        client = APIClient()
        client.force_authenticate(other)
        response = client.get(f"/api/corporate-actions/{action.id}/")
        self.assertEqual(response.status_code, 404)


class CorporateActionAPITests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="corp-action-api", password="test-password")
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def test_create_rejects_ratio_action_without_ratio(self):
        response = self.client.post("/api/corporate-actions/", {
            "action_type": "STOCK_SPLIT",
            "security_name": "Example Limited",
            "isin": "INE000A01000",
            "ex_date": "2025-06-15",
        }, format="json")
        self.assertEqual(response.status_code, 400)

    def test_pending_action_is_not_applied(self):
        response = self.client.post("/api/corporate-actions/", {
            "action_type": "STOCK_SPLIT",
            "security_name": "Example Limited",
            "isin": "INE000A01000",
            "ex_date": "2025-06-15",
            "old_units": "1",
            "new_units": "2",
        }, format="json")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["status"], "PENDING")
        self.assertIsNone(response.data["quantity_factor"])
