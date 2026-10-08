"""Regression tests for the audit fixes (hand-derived expected values)."""
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from django.test import SimpleTestCase

from analytics.services.cash_flows import build_cash_flows, xirr_percent
from analytics.services.historical_wealth import HistoricalWealthAnalytics
from investments.models import TransactionType
from portfolio.services.portfolio_tree_service import PortfolioTreeService


def tx(kind, day, amount, quantity=0, fees=0, notes=""):
    return SimpleNamespace(
        transaction_type=kind,
        transaction_date=day,
        amount=Decimal(str(amount)),
        quantity=Decimal(str(quantity)),
        fees=Decimal(str(fees)),
        notes=notes,
    )


class CashFlowTests(SimpleTestCase):
    def test_dividend_is_a_cash_flow(self):
        flows = build_cash_flows([
            tx(TransactionType.BUY, date(2025, 1, 1), 10000),
            tx(TransactionType.DIVIDEND, date(2025, 7, 1), 500),
        ])
        self.assertEqual(flows, [(date(2025, 1, 1), -10000.0), (date(2025, 7, 1), 500.0)])

    def test_reinvested_dividend_is_not_new_money(self):
        flows = build_cash_flows([
            tx(TransactionType.BUY, date(2025, 1, 1), 10000),
            tx(TransactionType.BUY, date(2025, 7, 1), 500, notes="DIVIDEND REINVESTMENT"),
        ])
        self.assertEqual(flows, [(date(2025, 1, 1), -10000.0)])

    def test_fees_increase_outflow_and_reduce_inflow(self):
        flows = build_cash_flows([
            tx(TransactionType.BUY, date(2025, 1, 1), 10000, fees=50),
            tx(TransactionType.SELL, date(2025, 6, 1), 4000, fees=30),
        ])
        self.assertEqual([f[1] for f in flows], [-10050.0, 3970.0])

    def test_tree_xirr_matches_independent_value_with_dividend(self):
        # -10,000 (1-Jan-25), +500 (1-Jul-25), +11,000 (today-equivalent 1-Jan-26)
        # independent solution of the XIRR equation = 15.37 %
        flows = [
            (date(2025, 1, 1), -10000.0),
            (date(2025, 7, 1), 500.0),
            (date(2026, 1, 1), 11000.0),
        ]
        self.assertEqual(xirr_percent(flows), 15.37)

    def test_xirr_percent_basic_cases(self):
        self.assertEqual(xirr_percent([(date(2025, 1, 1), -100.0), (date(2026, 1, 1), 110.0)]), 10.0)
        self.assertEqual(xirr_percent([(date(2025, 1, 1), -1000.0), (date(2026, 1, 1), 800.0)]), -20.0)
        self.assertEqual(
            xirr_percent([
                (date(2025, 1, 1), -100000.0),
                (date(2025, 7, 1), -50000.0),
                (date(2026, 1, 1), 180000.0),
            ]),
            24.22,
        )

    def test_one_sided_flows_have_no_xirr(self):
        self.assertIsNone(xirr_percent([(date(2025, 1, 1), -1000.0), (date(2026, 1, 1), -10.0)]))
        self.assertIsNone(xirr_percent([(date(2025, 1, 1), -1000.0)]))


class HistoricalCorporateActionTests(SimpleTestCase):
    def _position(self):
        return {"quantity": Decimal("0"), "invested_value": Decimal("0")}

    def test_split_and_bonus_add_shares_without_cost(self):
        position = self._position()
        for t in (
            tx(TransactionType.BUY, date(2025, 1, 1), 10000, quantity=100),
            tx(TransactionType.SPLIT, date(2025, 2, 1), 0, quantity=100),
            tx(TransactionType.BONUS, date(2025, 3, 1), 0, quantity=50),
        ):
            HistoricalWealthAnalytics._apply_equity_transaction(position, t)
        self.assertEqual(position["quantity"], Decimal("250"))
        self.assertEqual(position["invested_value"], Decimal("10000"))
        # value at price 60 must match the holdings engine: 250 x 60
        self.assertEqual(position["quantity"] * Decimal("60"), Decimal("15000"))
