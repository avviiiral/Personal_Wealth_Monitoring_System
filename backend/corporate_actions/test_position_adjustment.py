from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase

from investments.models import Asset, AssetCategory, Transaction, TransactionType
from .models import CorporateAction, CorporateActionStatus, CorporateActionType
from .position_adjustment import calculate_position_with_actions, confirmed_ratio_actions


class CorporateActionPositionAdjustmentTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="position-adjustment", password="test-password")
        self.asset = Asset.objects.create(
            owner=self.user,
            name="Example Limited",
            category=AssetCategory.STOCK,
            symbol="EXAMPLE",
            isin="INE000A01000",
        )

    def transaction(self, kind, tx_date, quantity, amount, notes=""):
        return Transaction.objects.create(
            owner=self.user,
            asset=self.asset,
            transaction_type=kind,
            transaction_date=tx_date,
            quantity=Decimal(str(quantity)),
            price_per_unit=Decimal("10"),
            amount=Decimal(str(amount)),
            notes=notes,
        )

    def split(self, *, ex_date=date(2025, 6, 15), status=CorporateActionStatus.CONFIRMED):
        return CorporateAction.objects.create(
            owner=self.user,
            action_type=CorporateActionType.STOCK_SPLIT,
            security_name="Example Limited",
            isin="INE000A01000",
            symbol="EXAMPLE",
            ex_date=ex_date,
            old_units=Decimal("1"),
            new_units=Decimal("2"),
            status=status,
        )

    def test_split_adjusts_existing_units_before_later_sale_without_changing_cost_basis(self):
        self.transaction(TransactionType.BUY, date(2025, 1, 1), 10, 100)
        self.transaction(TransactionType.SELL, date(2025, 7, 1), 5, 50)
        self.split()

        actions = confirmed_ratio_actions(asset=self.asset, owner=self.user)
        position = calculate_position_with_actions(
            Transaction.objects.filter(asset=self.asset).order_by("transaction_date", "created_at", "id"),
            actions,
        )
        self.assertEqual(position["quantity"], Decimal("15"))
        self.assertEqual(position["invested_value"], Decimal("50"))
        self.assertEqual(position["average_cost"], Decimal("50") / Decimal("15"))

    def test_pending_split_does_not_change_position(self):
        self.transaction(TransactionType.BUY, date(2025, 1, 1), 10, 100)
        self.split(status=CorporateActionStatus.PENDING)
        actions = confirmed_ratio_actions(asset=self.asset, owner=self.user)
        position = calculate_position_with_actions(
            Transaction.objects.filter(asset=self.asset).order_by("transaction_date", "created_at", "id"),
            actions,
        )
        self.assertEqual(position["quantity"], Decimal("10"))
        self.assertEqual(position["invested_value"], Decimal("100"))

    def test_as_of_date_excludes_future_corporate_action(self):
        self.transaction(TransactionType.BUY, date(2025, 1, 1), 10, 100)
        self.split()
        actions = confirmed_ratio_actions(
            asset=self.asset, owner=self.user, as_of_date=date(2025, 5, 1)
        )
        position = calculate_position_with_actions(
            Transaction.objects.filter(asset=self.asset).order_by("transaction_date", "created_at", "id"),
            actions,
        )
        self.assertEqual(position["quantity"], Decimal("10"))
