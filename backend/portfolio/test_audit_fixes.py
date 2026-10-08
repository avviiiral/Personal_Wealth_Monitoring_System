"""DB-backed regression tests for the audit fixes."""
from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase

from investments.models import Asset, AssetCategory, Transaction, TransactionType
from portfolio.services.portfolio_position_engine import PortfolioPositionEngine
from users.models import FamilyGroup


class PositionEngineAuditTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("audit_user", password="x")
        self.family = FamilyGroup.objects.create(name="Audit Family", created_by=self.user)
        self.asset = Asset.objects.create(
            owner=self.user, family=self.family, name="Audit Stock",
            category=AssetCategory.STOCK, symbol="AUD",
        )

    def _buy(self, day, qty, price):
        Transaction.objects.create(
            owner=self.user, family=self.family, family_name="Audit Family",
            portfolio="P", asset=self.asset, transaction_type=TransactionType.BUY,
            transaction_date=day, quantity=Decimal(qty), price_per_unit=Decimal(price),
            amount=Decimal(qty) * Decimal(price),
        )

    def test_no_price_position_is_carried_at_cost_with_zero_gain(self):
        self._buy(date(2026, 1, 1), "10", "100")
        position = PortfolioPositionEngine.rebuild_position(
            family=self.family, family_name="Audit Family", portfolio="P", asset=self.asset,
        )
        self.assertEqual(position.invested_value, Decimal("1000"))
        self.assertEqual(position.current_value, Decimal("1000"))
        self.assertEqual(position.gain, Decimal("0"))

    def test_rebuild_all_builds_one_position_per_asset_not_per_transaction(self):
        for month in range(1, 7):
            self._buy(date(2026, month, 1), "10", "100")
        positions = PortfolioPositionEngine.rebuild_all_for_family(self.family)
        self.assertEqual(len(positions), 1)
        self.assertEqual(positions[0].quantity, Decimal("60"))
