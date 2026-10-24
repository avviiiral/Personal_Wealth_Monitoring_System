"""Characterization tests for shared investor cash-flow and XIRR behavior.

These tests pin existing semantics before any cross-view calculation refactor.
They intentionally test the shared helper/solver, not live portfolio valuation.
"""
from datetime import date
from types import SimpleNamespace

from django.test import SimpleTestCase

from investments.models import TransactionType
from analytics.services.cash_flows import (
    DIVIDEND_REINVESTMENT_NOTE,
    build_cash_flows,
    transaction_cash_flow,
    xirr_percent,
)
from analytics.services.xirr import XIRRCalculator


class TransactionCashFlowCharacterizationTests(SimpleTestCase):
    def _transaction(self, transaction_type, amount, fees=0, notes=""):
        return SimpleNamespace(
            transaction_type=transaction_type,
            transaction_date=date(2021, 1, 1),
            amount=amount,
            fees=fees,
            notes=notes,
        )

    def test_outflow_includes_fees_as_negative_investor_cash_flow(self):
        tx = self._transaction(TransactionType.BUY, 1000, fees=10)

        self.assertEqual(
            transaction_cash_flow(tx),
            (date(2021, 1, 1), -1010.0),
        )

    def test_inflow_deducts_fees_from_investor_cash_flow(self):
        tx = self._transaction(TransactionType.SELL, 1200, fees=10)

        self.assertEqual(
            transaction_cash_flow(tx),
            (date(2021, 1, 1), 1190.0),
        )

    def test_dividend_reinvestment_is_not_external_cash_flow(self):
        tx = self._transaction(
            TransactionType.BUY,
            500,
            fees=0,
            notes=DIVIDEND_REINVESTMENT_NOTE,
        )

        self.assertIsNone(transaction_cash_flow(tx))

    def test_unrecognized_transaction_type_is_ignored(self):
        tx = self._transaction("UNSUPPORTED", 500)

        self.assertIsNone(transaction_cash_flow(tx))

    def test_builder_preserves_date_order_and_skips_reinvestment(self):
        first = self._transaction(TransactionType.BUY, 1000)
        second = self._transaction(
            TransactionType.BUY,
            500,
            notes=DIVIDEND_REINVESTMENT_NOTE,
        )
        third = SimpleNamespace(
            transaction_type=TransactionType.SELL,
            transaction_date=date(2022, 1, 1),
            amount=1100,
            fees=0,
            notes="",
        )

        self.assertEqual(
            build_cash_flows([first, second, third]),
            [
                (date(2021, 1, 1), -1000.0),
                (date(2022, 1, 1), 1100.0),
            ],
        )

    def test_one_year_ten_percent_return_has_expected_xirr(self):
        flows = [
            (date(2021, 1, 1), -1000.0),
            (date(2022, 1, 1), 1100.0),
        ]

        self.assertAlmostEqual(XIRRCalculator.calculate(flows), 0.10, places=5)
        self.assertEqual(xirr_percent(flows), 10.0)

    def test_xirr_percent_preserves_four_decimal_places(self):
        flows = [
            (date(2021, 1, 1), -1000.0),
            (date(2022, 1, 1), 1101.23456),
        ]

        self.assertEqual(xirr_percent(flows), 10.1235)

    def test_xirr_is_undefined_without_both_cash_flow_signs(self):
        self.assertIsNone(XIRRCalculator.calculate([]))
        self.assertIsNone(XIRRCalculator.calculate([
            (date(2021, 1, 1), -1000.0),
        ]))
        self.assertIsNone(XIRRCalculator.calculate([
            (date(2021, 1, 1), -1000.0),
            (date(2022, 1, 1), -1100.0),
        ]))
