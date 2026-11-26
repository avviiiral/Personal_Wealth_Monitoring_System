"""Ledger reconciliation: detection, hierarchy, accounting, duplicates and workflow."""
import threading
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import connection
from django.test import TestCase, TransactionTestCase, skipUnlessDBFeature
from rest_framework.test import APIClient

from analytics.services.cash_flows import build_cash_flows
from analytics.services.historical_wealth import HistoricalWealthAnalytics
from analytics.services.portfolio_analytics import PortfolioAnalytics
from investments.models import (
    Asset, AssetCategory, AssetUnderlyingHolding, Holding, PendingRestructuring, PortfolioPosition, Transaction,
    TransactionType,
)
from mutual_funds.models import MutualFundScheme, MutualFundTransaction
from mutual_funds.underlying_models import MutualFundUnderlying
from portfolio.mis_report_service import MISReportService
from portfolio.services.holding_engine import HoldingCalculationEngine
from portfolio.services.portfolio_position_engine import PortfolioPositionEngine
from portfolio.services.portfolio_tree_service import PortfolioTreeService
from users.models import FamilyGroup, Role

from . import reconciliation as ledger
from .models import (
    CorporateAction, CorporateActionLedgerEntry, CorporateActionNotification, CorporateActionPosition,
    CorporateActionReconciliation, CorporateActionStatus, CorporateActionType,
)
from .services import discover_exposures_and_reconcile, upsert_corporate_action

D = Decimal
ISIN = "INE000ABC011"
SUCCESSOR_ISIN = "INE000XYZ022"
EX_DATE = date(2025, 6, 15)


def make_user(username, role=Role.ADMIN):
    user = User.objects.create_user(username=username, password="test-password")
    user.profile.role = role
    user.profile.save(update_fields=["role"])
    return user


class LedgerHelpers:
    """Fixtures shared by TestCase and TransactionTestCase classes."""

    def setUp(self):
        self.user = make_user("ca-ledger-owner")
        self.family = FamilyGroup.objects.create(name="Family One")
        self.user.profile.family_groups.add(self.family)
        self.user.profile.active_family_group = self.family
        self.user.profile.save(update_fields=["active_family_group", "updated_at"])
        self.stock = self.make_asset()

    def make_asset(self, name="ABC Ltd", isin=ISIN, category=AssetCategory.STOCK, family=None, owner=None, symbol="ABC.NS"):
        return Asset.objects.create(
            owner=owner or self.user, family=family or self.family, name=name, category=category,
            isin=isin, symbol=symbol, currency="INR", is_active=True,
        )

    def tx(self, kind, tx_date, quantity, amount=None, *, asset=None, family=None, owner=None,
           family_name="Member A", portfolio="Direct Equity", asset_class="Equity",
           sub_class="Listed Equity", asset_name="Direct Equity", underlying="ABC Ltd", source="EXCEL", price=None):
        quantity = D(str(quantity))
        amount = D(str(amount if amount is not None else quantity * 10))
        return Transaction.objects.create(
            owner=owner or self.user, family=family or self.family, asset=asset or self.stock,
            family_name=family_name, portfolio=portfolio, asset_class=asset_class, sub_class=sub_class,
            asset_name=asset_name, underlying=underlying, advisors="Advisor",
            transaction_type=kind, transaction_date=tx_date, quantity=quantity,
            price_per_unit=D(str(price)) if price is not None else (amount / quantity if quantity else D("0")),
            amount=amount, fees=D("0"), source=source,
        )

    def make_action(self, owner=None, **overrides):
        values = {
            "owner": owner or self.user, "action_type": CorporateActionType.STOCK_SPLIT,
            "security_name": "ABC Ltd", "isin": ISIN, "symbol": "ABC", "exchange": "NSE",
            "ex_date": EX_DATE, "old_units": D("1"), "new_units": D("2"),
            "status": CorporateActionStatus.PENDING, "source": "NSE_CORPORATE_ACTIONS",
        }
        values.update(overrides)
        return CorporateAction.objects.create(**values)

    def reconcile(self, action):
        discover_exposures_and_reconcile(action)
        return CorporateActionReconciliation.objects.get(action=action, owner=action.owner)

    def accept(self, reconciliation, owner=None):
        return ledger.accept_reconciliation(owner=owner or reconciliation.owner, reconciliation_id=reconciliation.id)

    def positions(self, reconciliation):
        return {
            (p.family_name, p.portfolio, p.asset_class, p.sub_class, p.asset_name): p
            for p in CorporateActionPosition.objects.filter(reconciliation=reconciliation)
        }

    def ca_rows(self, **filters):
        return Transaction.objects.filter(corporate_action_entry__isnull=False, **filters)


class LedgerFixture(LedgerHelpers, TestCase):
    pass


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------

class DetectionTests(LedgerFixture):
    def test_exact_isin_detection_creates_notification_but_no_transaction(self):
        self.tx("BUY", date(2025, 1, 1), 100, 1000)
        before = Transaction.objects.count()
        reconciliation = self.reconcile(self.make_action())
        self.assertEqual(reconciliation.status, "PENDING")
        self.assertEqual(reconciliation.suggested_quantity, D("100"))
        self.assertTrue(CorporateActionNotification.objects.filter(reconciliation=reconciliation).exists())
        self.assertEqual(Transaction.objects.count(), before)
        position = CorporateActionPosition.objects.get(reconciliation=reconciliation)
        self.assertEqual(position.status, "PENDING")
        self.assertEqual(position.quantity_delta, D("100"))
        self.assertEqual(position.planned_entries[0]["transaction_type"], "SPLIT")

    def test_detection_never_changes_any_holding(self):
        self.tx("BUY", date(2025, 1, 1), 100, 1000)
        HoldingCalculationEngine.rebuild_holding(self.stock)
        self.reconcile(self.make_action(status=CorporateActionStatus.CONFIRMED))
        self.assertEqual(HoldingCalculationEngine.calculate_position(self.stock)["quantity"], D("100"))
        self.assertEqual(Holding.objects.get(asset=self.stock).quantity, D("100"))

    def test_security_no_longer_held_is_still_entitled_for_its_historical_holding(self):
        self.tx("BUY", date(2024, 1, 1), 50, 500)
        self.tx("SELL", date(2025, 9, 1), 100, 1200)  # sold after the split
        self.assertEqual(HoldingCalculationEngine.calculate_position(self.stock)["quantity"], D("0"))
        reconciliation = self.reconcile(self.make_action())
        position = CorporateActionPosition.objects.get(reconciliation=reconciliation)
        self.assertEqual(position.status, "PENDING")
        self.assertEqual(position.eligible_quantity, D("50"))

    def test_purchase_after_the_ex_date_is_not_entitled(self):
        self.tx("BUY", date(2025, 1, 1), 10, 100)
        self.tx("BUY", date(2025, 6, 15), 40, 400)  # ex-date trade: post-split
        self.tx("BUY", date(2025, 7, 1), 40, 400)
        reconciliation = self.reconcile(self.make_action(action_type=CorporateActionType.BONUS))
        self.assertEqual(CorporateActionPosition.objects.get(reconciliation=reconciliation).eligible_quantity, D("10"))

    def test_eligibility_cut_off_boundary(self):
        # The day before the ex-date is entitled; the ex-date itself is not.
        self.tx("BUY", EX_DATE - timedelta(days=1), 7, 70)
        self.tx("BUY", EX_DATE, 11, 110)
        position = CorporateActionPosition.objects.get(reconciliation=self.reconcile(self.make_action()))
        self.assertEqual(position.eligible_quantity, D("7"))
        self.assertEqual(position.eligibility_cutoff, EX_DATE)

    def test_record_date_cut_off_boundary(self):
        record_date = date(2025, 6, 10)
        self.tx("BUY", record_date - timedelta(days=1), 3, 30)
        self.tx("BUY", record_date, 5, 50)
        position = CorporateActionPosition.objects.get(
            reconciliation=self.reconcile(self.make_action(record_date=record_date)))
        self.assertEqual(position.eligible_quantity, D("3"))

    def test_owner_with_an_empty_ledger_gets_no_reconciliation(self):
        action = self.make_action()
        stats = discover_exposures_and_reconcile(action)
        self.assertEqual((stats["direct"], stats["notifications"]), (0, 0))
        self.assertFalse(CorporateActionReconciliation.objects.filter(action=action).exists())

    def test_null_terms_and_optional_dates_never_book_anything(self):
        self.tx("BUY", date(2025, 1, 1), 10, 100)
        for action_type in (CorporateActionType.CASH_DIVIDEND, CorporateActionType.BONUS, CorporateActionType.MERGER):
            with self.subTest(action_type=action_type):
                action = self.make_action(action_type=action_type, old_units=None, new_units=None, cash_per_unit=None,
                                          record_date=None, payment_date=None, resulting_isin="")
                reconciliation = self.reconcile(action)
                self.assertEqual(reconciliation.status, "MANUAL_REVIEW")
                self.assertEqual(CorporateActionPosition.objects.get(reconciliation=reconciliation).planned_entries, [])
        self.assertFalse(Transaction.objects.filter(source="OTHER").exists())

    def test_only_post_event_rows_do_not_form_a_position(self):
        self.tx("BUY", EX_DATE, 10, 100)
        action = self.make_action()
        discover_exposures_and_reconcile(action)
        self.assertFalse(CorporateActionReconciliation.objects.filter(action=action).exists())

    def test_record_date_before_ex_date_uses_the_earlier_cut_off(self):
        self.tx("BUY", date(2025, 1, 1), 10, 100)
        self.tx("BUY", date(2025, 6, 12), 40, 400)
        reconciliation = self.reconcile(self.make_action(record_date=date(2025, 6, 10)))
        position = CorporateActionPosition.objects.get(reconciliation=reconciliation)
        self.assertEqual(position.eligible_quantity, D("10"))
        self.assertEqual(position.eligibility_cutoff, date(2025, 6, 10))

    def test_missing_isin_without_exact_candidates_creates_nothing(self):
        self.tx("BUY", date(2025, 1, 1), 10, 100)
        action = self.make_action(isin="", symbol="UNKNOWN", security_name="Unknown Industries")
        discover_exposures_and_reconcile(action)
        self.assertFalse(CorporateActionReconciliation.objects.filter(action=action).exists())

    def test_missing_isin_with_exact_symbol_requires_identity_approval(self):
        self.stock.symbol = "ABC"
        self.stock.save(update_fields=["symbol"])
        self.tx("BUY", date(2025, 1, 1), 10, 100)
        action = self.make_action(isin="")
        reconciliation = self.reconcile(action)
        self.assertEqual(reconciliation.status, "PENDING_USER_APPROVAL")
        self.assertEqual(reconciliation.candidate_asset_ids, [self.stock.id])
        self.assertFalse(self.ca_rows().exists())

    def test_manual_identity_approval_books_through_the_ledger_engine(self):
        from .services import decide_missing_isin_reconciliation

        self.stock.symbol = "ABC"
        self.stock.save(update_fields=["symbol"])
        self.tx("BUY", date(2025, 1, 1), 10, 100)
        reconciliation = self.reconcile(self.make_action(isin=""))
        approved = decide_missing_isin_reconciliation(
            owner=self.user, reconciliation_id=reconciliation.id, decision="APPROVE", asset_id=self.stock.id,
        )
        self.assertEqual(approved.status, "APPROVED")
        split = self.ca_rows().get()
        self.assertEqual(split.transaction_type, TransactionType.SPLIT)
        self.assertEqual(split.quantity, D("10"))

    def test_unrelated_family_holding_the_same_isin_is_never_included(self):
        other_user = make_user("ca-ledger-unrelated")
        other_family = FamilyGroup.objects.create(name="Unrelated family")
        other_user.profile.family_groups.add(other_family)
        other_asset = self.make_asset(family=other_family, owner=other_user)
        self.tx("BUY", date(2025, 1, 1), 10, 100)
        self.tx("BUY", date(2025, 1, 1), 999, 9990, asset=other_asset, family=other_family, owner=other_user)
        reconciliation = self.reconcile(self.make_action())
        position = CorporateActionPosition.objects.get(reconciliation=reconciliation)
        self.assertEqual(position.family_id, self.family.id)
        self.assertEqual(position.eligible_quantity, D("10"))
        self.accept(reconciliation)
        self.assertFalse(Transaction.objects.filter(family=other_family, source="OTHER").exists())
        # The other family only gets its own copy, reconciled separately.
        self.assertFalse(CorporateActionReconciliation.objects.filter(owner=other_user).exists())

    def test_provider_term_conflict_goes_to_manual_review(self):
        self.tx("BUY", date(2025, 1, 1), 10, 100)
        action = self.make_action()
        lookup = {"isin": ISIN, "action_type": action.action_type, "ex_date": EX_DATE}
        upsert_corporate_action(owner=self.user, lookup=lookup, defaults={"old_units": "1", "new_units": "5"})
        action.refresh_from_db()
        self.assertEqual(action.new_units, D("2"))  # stored terms are never overwritten
        self.assertIn("new_units", action.metadata["term_conflicts"])
        reconciliation = self.reconcile(action)
        self.assertEqual(reconciliation.status, "MANUAL_REVIEW")


# ---------------------------------------------------------------------------
# Portfolio hierarchy
# ---------------------------------------------------------------------------

class HierarchyTests(LedgerFixture):
    def test_same_underlying_across_names_classes_members_and_asset_records(self):
        alternative = self.make_asset(category=AssetCategory.OTHER)  # second Asset row, same ISIN
        rows = [
            ("Member A", "Equity", "Direct Equity", 100, self.stock),
            ("Member A", "Alternative", "Portfolio Alpha", 50, alternative),
            ("Member B", "Equity", "Direct Stocks", 200, self.stock),
            ("Member B", "Equity", "Portfolio Beta", 75, self.stock),
            ("Member C", "Equity", "Long Term Portfolio", 125, self.stock),
        ]
        for member, asset_class, asset_name, quantity, asset in rows:
            self.tx("BUY", date(2025, 1, 1), quantity, quantity * 10, asset=asset, family_name=member,
                    asset_class=asset_class, asset_name=asset_name, portfolio=asset_name)
        reconciliation = self.reconcile(self.make_action())
        positions = self.positions(reconciliation)
        self.assertEqual(len(positions), 5)
        self.assertTrue(all(p.status == "PENDING" for p in positions.values()))

        self.accept(reconciliation)
        for member, asset_class, asset_name, quantity, asset in rows:
            split = self.ca_rows(family_name=member, asset_class=asset_class, asset_name=asset_name).get()
            self.assertEqual(split.quantity, D(quantity))
            self.assertEqual(split.asset_id, asset.id)
            self.assertEqual(split.transaction_date, EX_DATE)
            position = PortfolioPosition.objects.get(family=self.family, family_name=member, portfolio=asset_name, asset=asset)
            self.assertEqual(position.quantity, D(quantity * 2))
            self.assertEqual(position.invested_value, D(quantity * 10))
        self.assertEqual(Holding.objects.get(asset=self.stock).quantity, D("1000"))
        self.assertEqual(Holding.objects.get(asset=alternative).quantity, D("100"))

    def test_same_underlying_under_different_subclasses_are_separate_positions(self):
        self.tx("BUY", date(2025, 1, 1), 10, 100, sub_class="Large Cap")
        self.tx("BUY", date(2025, 1, 1), 20, 200, sub_class="Mid Cap")
        reconciliation = self.reconcile(self.make_action())
        self.assertEqual(len(self.positions(reconciliation)), 2)
        self.accept(reconciliation)
        self.assertEqual(self.ca_rows(sub_class="Large Cap").get().quantity, D("10"))
        self.assertEqual(self.ca_rows(sub_class="Mid Cap").get().quantity, D("20"))

    def test_multiple_asset_records_inside_one_position_are_legs(self):
        duplicate = self.make_asset(name="ABC Limited")
        self.tx("BUY", date(2025, 1, 1), 10, 100)
        self.tx("BUY", date(2025, 2, 1), 5, 50, asset=duplicate)
        reconciliation = self.reconcile(self.make_action())
        position = CorporateActionPosition.objects.get(reconciliation=reconciliation)
        self.assertCountEqual(position.asset_ids, [self.stock.id, duplicate.id])
        self.accept(reconciliation)
        self.assertEqual(self.ca_rows(asset=self.stock).get().quantity, D("10"))
        self.assertEqual(self.ca_rows(asset=duplicate).get().quantity, D("5"))

    def test_selected_family_does_not_limit_or_redirect_the_reconciliation(self):
        selected = FamilyGroup.objects.create(name="Selected elsewhere")
        self.user.profile.family_groups.add(selected)
        self.user.profile.active_family_group = selected
        self.user.profile.save(update_fields=["active_family_group", "updated_at"])
        self.tx("BUY", date(2025, 1, 1), 10, 100)
        reconciliation = self.reconcile(self.make_action())
        self.accept(reconciliation)
        split = self.ca_rows().get()
        self.assertEqual(split.family_id, self.family.id)
        self.assertEqual(PortfolioPosition.objects.get(family=self.family, asset=self.stock).quantity, D("20"))
        self.assertFalse(PortfolioPosition.objects.filter(family=selected).exists())

    def test_positions_in_several_families_are_booked_in_their_own_family(self):
        second = FamilyGroup.objects.create(name="Family Two")
        self.user.profile.family_groups.add(second)
        second_asset = self.make_asset(family=second)
        self.tx("BUY", date(2025, 1, 1), 10, 100)
        self.tx("BUY", date(2025, 1, 1), 30, 300, asset=second_asset, family=second)
        self.accept(self.reconcile(self.make_action()))
        self.assertEqual(self.ca_rows(family=self.family).get().quantity, D("10"))
        self.assertEqual(self.ca_rows(family=second).get().quantity, D("30"))
        self.assertEqual(Holding.objects.get(asset=second_asset).quantity, D("60"))


# ---------------------------------------------------------------------------
# Accounting through every engine
# ---------------------------------------------------------------------------

class AccountingTests(LedgerFixture):
    def assert_engines(self, asset, quantity, invested, *, family_name="Member A", portfolio="Direct Equity"):
        holding = HoldingCalculationEngine.calculate_position(asset)
        position = PortfolioPositionEngine.calculate_position(self.family, family_name, portfolio, asset)
        rows = list(Transaction.objects.filter(asset=asset, family=self.family, family_name=family_name, portfolio=portfolio))
        tree = PortfolioTreeService._calculate_position(rows)
        mis_quantity, mis_invested = MISReportService._position_at(rows, date(2030, 1, 1))
        history = HistoricalWealthAnalytics.calculate_equity_position_as_of(self.user, asset, date(2030, 1, 1))
        analytics = PortfolioAnalytics.calculate_position_as_of(asset, date(2030, 1, 1))
        for name, result_quantity, result_invested in (
            ("holding", holding["quantity"], holding["invested_value"]),
            ("position", position["quantity"], position["invested_value"]),
            ("tree", tree["quantity"], tree["invested_value"]),
            ("mis", mis_quantity, mis_invested),
            ("history", history["quantity"], history["invested_value"]),
            ("analytics", analytics["quantity"], analytics["invested_value"]),
        ):
            with self.subTest(engine=name):
                self.assertEqual(result_quantity.quantize(D("0.0001")), D(str(quantity)).quantize(D("0.0001")))
                self.assertEqual(result_invested.quantize(D("0.01")), D(str(invested)).quantize(D("0.01")))

    def test_bonus_adds_shares_without_cost_in_every_engine(self):
        self.tx("BUY", date(2025, 1, 1), 100, 1000)
        self.accept(self.reconcile(self.make_action(action_type=CorporateActionType.BONUS)))
        bonus = self.ca_rows().get()
        self.assertEqual((bonus.transaction_type, bonus.quantity, bonus.amount), (TransactionType.BONUS, D("200"), D("0")))
        self.assert_engines(self.stock, 300, 1000)

    def test_split_with_partial_sells_before_and_after_the_ex_date(self):
        self.tx("BUY", date(2025, 1, 1), 100, 1000)
        self.tx("SELL", date(2025, 3, 1), 20, 300)
        self.tx("SELL", date(2025, 6, 15), 10, 60)  # ex-date sale is post-split
        self.tx("SELL", date(2025, 7, 1), 50, 400)
        reconciliation = self.reconcile(self.make_action())
        self.assertEqual(CorporateActionPosition.objects.get(reconciliation=reconciliation).eligible_quantity, D("80"))
        self.accept(reconciliation)
        self.assertEqual(self.ca_rows().get().quantity, D("80"))
        # 80 -> 160 at 800 cost, then 60 sold at average cost 5.
        self.assert_engines(self.stock, 100, 500)

    def test_reverse_split_reduces_the_holding_in_every_engine(self):
        self.tx("BUY", date(2025, 1, 1), 1000, 50000)
        reconciliation = self.reconcile(self.make_action(
            action_type=CorporateActionType.REVERSE_SPLIT, old_units=D("10"), new_units=D("1")))
        self.accept(reconciliation)
        split = self.ca_rows().get()
        self.assertEqual(split.quantity, D("-900"))
        self.assert_engines(self.stock, 100, 50000)
        self.assertEqual(Holding.objects.get(asset=self.stock).average_cost, D("500"))
        self.tx("SELL", date(2025, 8, 1), 40, 30000)
        self.assert_engines(self.stock, 60, 30000)

    def test_fractional_reverse_split_without_cash_in_lieu_price_stays_unapplied(self):
        self.tx("BUY", date(2025, 1, 1), 1005, 1005)
        reconciliation = self.reconcile(self.make_action(
            action_type=CorporateActionType.REVERSE_SPLIT, old_units=D("10"), new_units=D("1")))
        self.assertEqual(reconciliation.status, "MANUAL_REVIEW")
        with self.assertRaises(ValidationError):
            self.accept(reconciliation)
        self.assertFalse(self.ca_rows().exists())
        self.assert_engines(self.stock, 1005, 1005)

    def test_cash_dividend_is_income_without_quantity_change(self):
        self.tx("BUY", date(2025, 1, 1), 100, 1000)
        reconciliation = self.reconcile(self.make_action(
            action_type=CorporateActionType.CASH_DIVIDEND, old_units=None, new_units=None,
            cash_per_unit=D("2.5"), payment_date=date(2025, 7, 10)))
        self.accept(reconciliation)
        dividend = self.ca_rows().get()
        self.assertEqual(dividend.transaction_type, TransactionType.DIVIDEND)
        self.assertEqual(dividend.amount, D("250"))
        self.assertEqual(dividend.transaction_date, date(2025, 7, 10))
        self.assert_engines(self.stock, 100, 1000)
        self.assertIn((date(2025, 7, 10), 250.0), build_cash_flows([dividend]))

    def test_merger_closes_the_original_and_carries_cost_to_the_successor(self):
        self.tx("BUY", date(2025, 1, 1), 100, 7000)
        reconciliation = self.reconcile(self.make_action(
            action_type=CorporateActionType.MERGER, old_units=D("5"), new_units=D("2"),
            resulting_isin=SUCCESSOR_ISIN, resulting_symbol="XYZ", resulting_security_name="XYZ Ltd"))
        self.assertEqual(reconciliation.status, "PENDING")
        self.accept(reconciliation)
        successor = Asset.objects.get(family=self.family, isin=SUCCESSOR_ISIN)
        sell = self.ca_rows(asset=self.stock).get()
        buy = self.ca_rows(asset=successor).get()
        self.assertEqual((sell.transaction_type, sell.quantity, sell.amount), (TransactionType.SELL, D("100"), D("7000")))
        self.assertEqual((buy.transaction_type, buy.quantity, buy.amount), (TransactionType.BUY, D("40"), D("7000")))
        self.assertEqual(buy.underlying, "XYZ Ltd")
        self.assert_engines(self.stock, 0, 0)
        successor_position = HoldingCalculationEngine.calculate_position(successor)
        self.assertEqual(successor_position["quantity"], D("40"))
        self.assertEqual(successor_position["invested_value"], D("7000"))
        # Equal and opposite same-day flows: XIRR inputs are unchanged.
        self.assertEqual(sum(amount for _, amount in build_cash_flows([sell, buy])), 0)

    def test_merger_with_later_activity_in_the_original_security_needs_review(self):
        self.tx("BUY", date(2025, 1, 1), 100, 7000)
        self.tx("SELL", date(2025, 7, 1), 10, 800)
        reconciliation = self.reconcile(self.make_action(
            action_type=CorporateActionType.MERGER, old_units=D("1"), new_units=D("1"), resulting_isin=SUCCESSOR_ISIN))
        self.assertEqual(reconciliation.status, "MANUAL_REVIEW")

    def test_demerger_keeps_the_parent_and_allocates_cost_without_duplication(self):
        self.tx("BUY", date(2025, 1, 1), 100, 10000)
        reconciliation = self.reconcile(self.make_action(
            action_type=CorporateActionType.DEMERGER, old_units=D("2"), new_units=D("1"),
            resulting_isin=SUCCESSOR_ISIN, resulting_security_name="Spinco Ltd", cost_allocation_ratio=D("0.25")))
        self.accept(reconciliation)
        child = Asset.objects.get(family=self.family, isin=SUCCESSOR_ISIN)
        self.assert_engines(self.stock, 100, 7500)
        child_position = HoldingCalculationEngine.calculate_position(child)
        self.assertEqual((child_position["quantity"], child_position["invested_value"]), (D("50"), D("2500")))
        total_cost = (HoldingCalculationEngine.calculate_position(self.stock)["invested_value"]
                      + child_position["invested_value"])
        self.assertEqual(total_cost, D("10000"))

    def test_demerger_without_cost_allocation_is_manual_and_cannot_be_accepted(self):
        self.tx("BUY", date(2025, 1, 1), 100, 10000)
        reconciliation = self.reconcile(self.make_action(
            action_type=CorporateActionType.DEMERGER, old_units=D("1"), new_units=D("1"), resulting_isin=SUCCESSOR_ISIN))
        self.assertEqual(reconciliation.status, "MANUAL_REVIEW")
        position = CorporateActionPosition.objects.get(reconciliation=reconciliation)
        self.assertIn("cost allocation", position.reason)
        with self.assertRaises(ValidationError):
            self.accept(reconciliation)
        self.assertFalse(Transaction.objects.filter(source="OTHER").exists())

    def test_cost_basis_is_preserved_by_split_and_bonus(self):
        self.tx("BUY", date(2025, 1, 1), 100, 1234.5678)
        self.accept(self.reconcile(self.make_action()))
        self.accept(self.reconcile(self.make_action(action_type=CorporateActionType.BONUS, ex_date=date(2025, 9, 1))))
        position = HoldingCalculationEngine.calculate_position(self.stock)
        self.assertEqual(position["quantity"], D("600"))
        self.assertEqual(position["invested_value"], D("1234.5678"))


# ---------------------------------------------------------------------------
# Duplicate prevention
# ---------------------------------------------------------------------------

class DuplicatePreventionTests(LedgerFixture):
    def test_uploaded_action_on_the_event_date_is_already_recorded_and_untouched(self):
        self.tx("BUY", date(2025, 1, 1), 100, 1000)
        uploaded = self.tx("SPLIT", EX_DATE, 100, 0)
        reconciliation = self.reconcile(self.make_action())
        self.assertEqual(reconciliation.status, "ALREADY_RECORDED")
        position = CorporateActionPosition.objects.get(reconciliation=reconciliation)
        self.assertEqual(position.matched_transaction_ids, [uploaded.id])
        with self.assertRaises(ValidationError):
            self.accept(reconciliation)
        uploaded.refresh_from_db()
        self.assertEqual((uploaded.quantity, uploaded.source, uploaded.amount), (D("100"), "EXCEL", D("0")))
        self.assertEqual(Transaction.objects.filter(transaction_type="SPLIT").count(), 1)

    def test_uploaded_action_for_another_asset_name_does_not_suppress(self):
        self.tx("BUY", date(2025, 1, 1), 100, 1000)
        self.tx("SPLIT", EX_DATE, 100, 0, asset_name="Other PMS", portfolio="Other PMS")
        reconciliation = self.reconcile(self.make_action())
        self.assertEqual(self.positions(reconciliation)[("Member A", "Direct Equity", "Equity", "Listed Equity", "Direct Equity")].status, "PENDING")

    def test_uploaded_action_for_another_asset_class_does_not_suppress(self):
        self.tx("BUY", date(2025, 1, 1), 100, 1000)
        self.tx("SPLIT", EX_DATE, 100, 0, asset_class="Alternative")
        reconciliation = self.reconcile(self.make_action())
        self.assertEqual(self.positions(reconciliation)[("Member A", "Direct Equity", "Equity", "Listed Equity", "Direct Equity")].status, "PENDING")

    def test_only_the_member_without_an_uploaded_row_is_adjusted(self):
        self.tx("BUY", date(2025, 1, 1), 100, 1000, family_name="Member A")
        self.tx("BUY", date(2025, 1, 1), 40, 400, family_name="Member B")
        uploaded = self.tx("SPLIT", EX_DATE, 100, 0, family_name="Member A")
        reconciliation = self.reconcile(self.make_action())
        positions = self.positions(reconciliation)
        self.assertEqual(positions[("Member A", "Direct Equity", "Equity", "Listed Equity", "Direct Equity")].status, "ALREADY_RECORDED")
        self.assertEqual(positions[("Member B", "Direct Equity", "Equity", "Listed Equity", "Direct Equity")].status, "PENDING")
        _, counters = self.accept(reconciliation)
        self.assertEqual(self.ca_rows().get().family_name, "Member B")
        self.assertEqual(counters["recorded"], [uploaded.id])

    def test_one_portfolio_has_the_action_and_another_does_not(self):
        self.tx("BUY", date(2025, 1, 1), 100, 1000, portfolio="Broker One")
        self.tx("BUY", date(2025, 1, 1), 60, 600, portfolio="Broker Two")
        self.tx("SPLIT", EX_DATE, 100, 0, portfolio="Broker One")
        reconciliation = self.reconcile(self.make_action())
        positions = self.positions(reconciliation)
        self.assertEqual(positions[("Member A", "Broker One", "Equity", "Listed Equity", "Direct Equity")].status, "ALREADY_RECORDED")
        self.assertEqual(positions[("Member A", "Broker Two", "Equity", "Listed Equity", "Direct Equity")].status, "PENDING")
        self.accept(reconciliation)
        self.assertEqual(self.ca_rows().get().portfolio, "Broker Two")

    def test_uploaded_row_with_an_unidentifiable_portfolio_goes_to_manual_review(self):
        self.tx("BUY", date(2025, 1, 1), 100, 1000, portfolio="Broker One")
        self.tx("BUY", date(2025, 1, 1), 60, 600, portfolio="Broker Two")
        self.tx("SPLIT", EX_DATE, 100, 0, portfolio="Imported Portfolio")
        reconciliation = self.reconcile(self.make_action())
        self.assertTrue(all(p.status == "MANUAL_REVIEW" for p in self.positions(reconciliation).values()))

    def test_uploaded_dividend_dated_on_the_payment_date_counts_as_recorded(self):
        self.tx("BUY", date(2025, 1, 1), 100, 1000)
        self.tx("DIVIDEND", date(2025, 7, 8), 100, 250)
        reconciliation = self.reconcile(self.make_action(
            action_type=CorporateActionType.CASH_DIVIDEND, old_units=None, new_units=None, cash_per_unit=D("2.5")))
        self.assertEqual(reconciliation.status, "ALREADY_RECORDED")

    def test_a_row_booked_for_a_different_event_is_not_evidence(self):
        self.tx("BUY", date(2025, 1, 1), 100, 1000)
        interim = self.make_action(action_type=CorporateActionType.CASH_DIVIDEND, old_units=None, new_units=None,
                                   cash_per_unit=D("1"), ex_date=date(2025, 6, 1))
        self.accept(self.reconcile(interim))
        final = self.make_action(action_type=CorporateActionType.CASH_DIVIDEND, old_units=None, new_units=None,
                                 cash_per_unit=D("2"), ex_date=date(2025, 6, 20))
        self.assertEqual(self.reconcile(final).status, "PENDING")

    def test_repeated_scans_are_idempotent(self):
        self.tx("BUY", date(2025, 1, 1), 100, 1000)
        action = self.make_action()
        for _ in range(3):
            discover_exposures_and_reconcile(action)
        self.assertEqual(CorporateActionReconciliation.objects.filter(action=action).count(), 1)
        self.assertEqual(CorporateActionPosition.objects.filter(reconciliation__action=action).count(), 1)
        self.assertEqual(CorporateActionNotification.objects.filter(action=action).count(), 1)
        self.assertFalse(Transaction.objects.filter(source="OTHER").exists())

    def test_repeated_acceptance_never_duplicates(self):
        self.tx("BUY", date(2025, 1, 1), 100, 1000)
        reconciliation = self.reconcile(self.make_action())
        self.accept(reconciliation)
        again, counters = self.accept(reconciliation)
        self.assertEqual(again.status, "COMPLETED")
        self.assertEqual(counters["created"], [])
        self.assertEqual(self.ca_rows().count(), 1)

    def test_two_family_members_accepting_their_own_copies_book_once(self):
        member = make_user("ca-ledger-member")
        member.profile.family_groups.add(self.family)
        self.tx("BUY", date(2025, 1, 1), 100, 1000)
        mine = self.reconcile(self.make_action())
        theirs = self.reconcile(self.make_action(owner=member))
        self.accept(mine)
        ledger.refresh_reconciliation(theirs)
        theirs.refresh_from_db()
        self.assertEqual(theirs.status, "ALREADY_RECORDED")
        with self.assertRaises(ValidationError):
            self.accept(theirs, owner=member)
        self.assertEqual(Transaction.objects.filter(transaction_type="SPLIT").count(), 1)

    def test_concurrent_acceptance_is_stopped_by_the_deterministic_ledger_key(self):
        # Simulate the second of two racing acceptances: it does not yet see the
        # first one's rows as evidence, and still cannot create a duplicate.
        member = make_user("ca-ledger-racer")
        member.profile.family_groups.add(self.family)
        self.tx("BUY", date(2025, 1, 1), 100, 1000)
        mine = self.reconcile(self.make_action())
        theirs = self.reconcile(self.make_action(owner=member))
        self.accept(mine)
        with patch("corporate_actions.reconciliation._evidence_candidates", return_value=[]):
            _, counters = self.accept(theirs, owner=member)
        self.assertEqual(counters["created"], [])
        self.assertEqual(len(counters["reused"]), 1)
        self.assertEqual(Transaction.objects.filter(transaction_type="SPLIT").count(), 1)

    def test_rejected_reconciliation_is_not_reopened_by_later_scans(self):
        self.tx("BUY", date(2025, 1, 1), 100, 1000)
        action = self.make_action()
        reconciliation = self.reconcile(action)
        ledger.reject_reconciliation(owner=self.user, reconciliation_id=reconciliation.id, reason="Wrong ratio")
        discover_exposures_and_reconcile(action)
        reconciliation.refresh_from_db()
        self.assertEqual(reconciliation.status, "REJECTED")
        self.assertEqual(reconciliation.notes, "Wrong ratio")
        self.assertFalse(Transaction.objects.filter(source="OTHER").exists())

    def test_pending_restructuring_upload_is_not_treated_as_booked(self):
        self.tx("BUY", date(2025, 1, 1), 100, 7000)
        pending = PendingRestructuring.objects.create(
            family=self.family, owner=self.user, kind="MERGER", event_date=EX_DATE,
            family_name="Member A", asset_name="Direct Equity", source_key="x" * 64,
            legs=[{"role": "FROM", "underlying": "ABC Ltd", "isin": ISIN, "quantity": "100", "amount": "7000"},
                  {"role": "TO", "underlying": "XYZ Ltd", "isin": SUCCESSOR_ISIN, "quantity": "40", "amount": "7000"}],
        )
        reconciliation = self.reconcile(self.make_action(
            action_type=CorporateActionType.MERGER, old_units=D("5"), new_units=D("2"), resulting_isin=SUCCESSOR_ISIN))
        position = CorporateActionPosition.objects.get(reconciliation=reconciliation)
        self.assertEqual(position.status, "MANUAL_REVIEW")
        self.assertIn(f"#{pending.id}", position.reason)
        pending.refresh_from_db()
        self.assertEqual(pending.status, "PENDING")


# ---------------------------------------------------------------------------
# Workflow and API
# ---------------------------------------------------------------------------

class WorkflowTests(LedgerFixture):
    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def test_pending_or_verified_events_do_not_change_any_quantity(self):
        self.tx("BUY", date(2025, 1, 1), 100, 1000)
        self.make_action(status=CorporateActionStatus.CONFIRMED)
        self.make_action(action_type=CorporateActionType.BONUS, status=CorporateActionStatus.PENDING)
        self.assertEqual(HoldingCalculationEngine.calculate_position(self.stock)["quantity"], D("100"))
        rows = list(Transaction.objects.filter(asset=self.stock))
        self.assertEqual(PortfolioTreeService._calculate_position(rows)["quantity"], D("100"))

    def test_rejection_changes_nothing_financially(self):
        self.tx("BUY", date(2025, 1, 1), 100, 1000)
        HoldingCalculationEngine.rebuild_holding(self.stock)
        reconciliation = self.reconcile(self.make_action())
        response = self.client.post(f"/api/corporate-actions/reconciliations/{reconciliation.id}/reject/",
                                    {"reason": "Not applicable to my shares"}, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["status"], "REJECTED")
        self.assertFalse(Transaction.objects.filter(source="OTHER").exists())
        self.assertEqual(Holding.objects.get(asset=self.stock).quantity, D("100"))
        self.assertEqual(CorporateActionPosition.objects.get(reconciliation=reconciliation).status, "REJECTED")
        self.assertTrue(CorporateActionNotification.objects.get(reconciliation=reconciliation).dismissed)

    def test_acceptance_creates_audited_ledger_rows(self):
        self.tx("BUY", date(2025, 1, 1), 100, 1000)
        reconciliation = self.reconcile(self.make_action(source_reference="https://example.com/ca"))
        response = self.client.post(f"/api/corporate-actions/reconciliations/{reconciliation.id}/accept/",
                                    {"confirm": True, "proposal_fingerprint": reconciliation.proposal_fingerprint},
                                    format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["status"], "COMPLETED")
        split = Transaction.objects.get(id=response.data["created_transaction_ids"][0])
        self.assertEqual(split.source, "OTHER")
        self.assertEqual(len(split.source_key), 64)
        for text in (f"Corporate action #{reconciliation.action_id}", "ex-date 2025-06-15", ISIN, "Member A",
                     "ratio 1:2", "eligible quantity 100", "source NSE_CORPORATE_ACTIONS", "accepted by ca-ledger-owner"):
            self.assertIn(text, split.notes)
        entry = CorporateActionLedgerEntry.objects.get(transaction=split)
        self.assertEqual(entry.position.reconciliation_id, reconciliation.id)
        reconciliation.refresh_from_db()
        self.assertEqual(reconciliation.reviewed_by, self.user)
        self.assertIsNotNone(reconciliation.reviewed_at)
        self.assertEqual(PortfolioPosition.objects.get(asset=self.stock).quantity, D("200"))

    def test_accept_requires_explicit_confirmation(self):
        self.tx("BUY", date(2025, 1, 1), 100, 1000)
        reconciliation = self.reconcile(self.make_action())
        response = self.client.post(f"/api/corporate-actions/reconciliations/{reconciliation.id}/accept/", {}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Transaction.objects.filter(source="OTHER").exists())

    def test_stale_proposal_is_refused_and_refreshed(self):
        self.tx("BUY", date(2025, 1, 1), 100, 1000)
        reconciliation = self.reconcile(self.make_action())
        shown = reconciliation.proposal_fingerprint
        self.tx("BUY", date(2025, 2, 1), 50, 500)  # ledger changed after the proposal was shown
        response = self.client.post(f"/api/corporate-actions/reconciliations/{reconciliation.id}/accept/",
                                    {"confirm": True, "proposal_fingerprint": shown}, format="json")
        self.assertEqual(response.status_code, 409)
        self.assertFalse(Transaction.objects.filter(source="OTHER").exists())
        reconciliation.refresh_from_db()
        self.assertNotEqual(reconciliation.proposal_fingerprint, shown)
        self.assertEqual(reconciliation.suggested_quantity, D("150"))

    def test_viewer_cannot_accept(self):
        viewer = make_user("ca-ledger-viewer", role=Role.VIEWER)
        viewer.profile.family_groups.add(self.family)
        self.tx("BUY", date(2025, 1, 1), 100, 1000)
        reconciliation = self.reconcile(self.make_action(owner=viewer))
        client = APIClient()
        client.force_authenticate(viewer)
        response = client.post(f"/api/corporate-actions/reconciliations/{reconciliation.id}/accept/",
                               {"confirm": True}, format="json")
        self.assertEqual(response.status_code, 403)
        self.assertFalse(Transaction.objects.filter(source="OTHER").exists())

    def test_indirect_mutual_fund_exposure_never_creates_a_direct_transaction(self):
        scheme = MutualFundScheme.objects.create(scheme_name="Equity Fund", family=self.family, owner=self.user)
        MutualFundTransaction.objects.create(
            owner=self.user, family=self.family, scheme=scheme, transaction_type="PURCHASE",
            transaction_date=date(2025, 1, 1), units=D("10"), nav=D("100"), amount=D("1000"),
        )
        MutualFundUnderlying.objects.create(
            scheme=scheme, security_name="ABC Ltd", isin=ISIN, security_key=ISIN,
            percentage_of_nav=D("4.5"), portfolio_date=date(2025, 5, 31),
        )
        self.stock.delete()
        action = self.make_action()
        reconciliation = self.reconcile(action)
        self.assertEqual(reconciliation.status, "MANUAL_REVIEW")
        self.assertIn("Indirect", reconciliation.notes)
        self.assertTrue(CorporateActionNotification.objects.filter(reconciliation=reconciliation).exists())
        with self.assertRaises(ValidationError):
            self.accept(reconciliation)
        self.assertFalse(Transaction.objects.exists())
        response = self.client.get("/api/corporate-actions/notifications/")
        item = response.data["results"][0]
        self.assertFalse(item["direct_exposure"])
        self.assertFalse(item["reconciliation"]["can_accept"])

    def test_indirect_pms_underlying_is_separate_from_direct_positions(self):
        pms = self.make_asset(name="PMS Alpha", isin=None, category=AssetCategory.OTHER, symbol=None)
        AssetUnderlyingHolding.objects.create(family=self.family, asset=pms, stock_name="ABC Ltd", isin=ISIN,
                                              holding_percentage=D("6"))
        self.tx("BUY", date(2025, 1, 1), 100, 1000)
        reconciliation = self.reconcile(self.make_action())
        self.accept(reconciliation)
        self.assertEqual(self.ca_rows().count(), 1)
        self.assertEqual(self.ca_rows().get().asset_id, self.stock.id)
        self.assertFalse(Transaction.objects.filter(asset=pms).exists())

    def test_notification_payload_shows_the_calculation_and_includes_dividends(self):
        self.tx("BUY", date(2025, 1, 1), 100, 1000, family_name="Member A")
        self.tx("BUY", date(2025, 1, 1), 50, 500, family_name="Member B")
        self.tx("DIVIDEND", date(2025, 6, 20), 100, 250, family_name="Member A")
        self.reconcile(self.make_action(action_type=CorporateActionType.CASH_DIVIDEND, old_units=None,
                                        new_units=None, cash_per_unit=D("2.5")))
        response = self.client.get("/api/corporate-actions/notifications/")
        self.assertEqual(response.status_code, 200)
        item = response.data["results"][0]
        self.assertEqual(item["action"]["action_type"], "CASH_DIVIDEND")
        summary = item["reconciliation"]["summary"]
        self.assertEqual(summary["affected_family_members"], ["Member A", "Member B"])
        self.assertEqual(summary["already_recorded"], 1)
        pending = [p for p in item["reconciliation"]["positions"] if p["status"] == "PENDING"]
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["family_name"], "Member B")
        self.assertIn("50 entitled shares × 2.5", pending[0]["explanation"])
        self.assertTrue(item["reconciliation"]["can_accept"])
        recorded = [p for p in item["reconciliation"]["positions"] if p["status"] == "ALREADY_RECORDED"][0]
        self.assertEqual(recorded["matched_transactions"][0]["transaction_type"], "DIVIDEND")

    def test_booked_event_terms_cannot_be_edited_or_deleted(self):
        self.tx("BUY", date(2025, 1, 1), 100, 1000)
        reconciliation = self.reconcile(self.make_action())
        self.accept(reconciliation)
        url = f"/api/corporate-actions/{reconciliation.action_id}/"
        self.assertEqual(self.client.patch(url, {"new_units": "5"}, format="json").status_code, 409)
        self.assertEqual(self.client.delete(url).status_code, 409)
        self.assertEqual(self.client.patch(url, {"status": "CONFIRMED"}, format="json").status_code, 200)

    def test_completing_missing_terms_refreshes_the_proposal(self):
        self.tx("BUY", date(2025, 1, 1), 100, 10000)
        reconciliation = self.reconcile(self.make_action(
            action_type=CorporateActionType.DEMERGER, old_units=D("1"), new_units=D("1"), resulting_isin=SUCCESSOR_ISIN))
        self.assertEqual(reconciliation.status, "MANUAL_REVIEW")
        response = self.client.patch(f"/api/corporate-actions/{reconciliation.action_id}/",
                                     {"cost_allocation_ratio": "0.2"}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        reconciliation.refresh_from_db()
        self.assertEqual(reconciliation.status, "PENDING")
        self.assertFalse(Transaction.objects.filter(source="OTHER").exists())

    def test_rescan_never_resets_the_users_verification(self):
        self.tx("BUY", date(2025, 1, 1), 100, 1000)
        action = self.make_action(status=CorporateActionStatus.CONFIRMED)
        updated, created = upsert_corporate_action(
            owner=self.user, lookup={"isin": ISIN, "action_type": action.action_type, "ex_date": EX_DATE},
            defaults={"status": CorporateActionStatus.PENDING, "record_date": EX_DATE},
        )
        self.assertFalse(created)
        self.assertEqual(updated.status, CorporateActionStatus.CONFIRMED)
        self.assertEqual(updated.record_date, EX_DATE)  # a blank term is filled


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

class ReportingTests(LedgerFixture):
    def test_accepted_rows_appear_in_transaction_history_with_a_description(self):
        self.tx("BUY", date(2025, 1, 1), 100, 1000)
        self.accept(self.reconcile(self.make_action(action_type=CorporateActionType.BONUS)))
        client = APIClient()
        client.force_authenticate(self.user)
        response = client.get("/api/portfolio/transactions/")
        self.assertEqual(response.status_code, 200)
        bonus = [row for row in response.data["results"] if row["transaction_type"] == "BONUS"]
        self.assertEqual(len(bonus), 1)
        self.assertEqual(D(str(bonus[0]["quantity"])), D("200"))
        self.assertEqual(bonus[0]["transaction_date"], "2025-06-15")
        self.assertEqual(bonus[0]["isin"], ISIN)
        self.assertIn("Bonus issue 1:2", bonus[0]["corporate_action_description"])
        buy = [row for row in response.data["results"] if row["transaction_type"] == "BUY"][0]
        self.assertEqual(buy["corporate_action_description"], "")

    def test_portfolio_tree_quantity_cost_and_xirr_after_acceptance(self):
        from market_data.models import MarketPrice

        self.tx("BUY", date(2025, 1, 1), 100, 1000)
        MarketPrice.objects.create(asset=self.stock, date=date(2025, 12, 1), source="YAHOO_FINANCE", close_price=D("6"))
        before = PortfolioTreeService.build(owner=self.user, family_id=self.family.id)
        self.accept(self.reconcile(self.make_action()))
        after = PortfolioTreeService.build(owner=self.user, family_id=self.family.id)

        def node(result):
            return result["families"][0]["portfolios"][0]["asset_classes"][0]["sub_classes"][0]["assets"][0]

        self.assertEqual(node(after)["quantity"], 200.0)
        self.assertEqual(node(after)["invested_value"], 1000.0)
        self.assertEqual(node(after)["average_cost"], 5.0)
        self.assertEqual(node(before)["invested_value"], node(after)["invested_value"])
        # Accepting the split doubles the valued quantity at the post-split price.
        self.assertEqual(node(after)["current_value"], 1200.0)
        self.assertIsNotNone(node(after)["xirr"])


# ---------------------------------------------------------------------------
# Real concurrency (PostgreSQL row locks; SQLite serialises writers instead)
# ---------------------------------------------------------------------------

@skipUnlessDBFeature("has_select_for_update")
class ConcurrentAcceptanceTests(LedgerHelpers, TransactionTestCase):
    def test_simultaneous_acceptance_by_two_family_members_books_once(self):
        member = make_user("ca-ledger-concurrent")
        member.profile.family_groups.add(self.family)
        self.tx("BUY", date(2025, 1, 1), 100, 1000)
        reconciliations = [
            self.reconcile(self.make_action()),
            self.reconcile(self.make_action(owner=member)),
        ]
        barrier = threading.Barrier(len(reconciliations))
        outcomes = []

        def accept(reconciliation):
            try:
                barrier.wait(timeout=10)
                ledger.accept_reconciliation(owner=reconciliation.owner, reconciliation_id=reconciliation.id)
                outcomes.append("booked")
            except ValidationError:
                outcomes.append("already recorded")
            finally:
                connection.close()

        threads = [threading.Thread(target=accept, args=(item,)) for item in reconciliations]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)

        self.assertEqual(sorted(outcomes), ["already recorded", "booked"])
        self.assertEqual(Transaction.objects.filter(transaction_type="SPLIT").count(), 1)
        self.assertEqual(HoldingCalculationEngine.calculate_position(self.stock)["quantity"], D("200"))
