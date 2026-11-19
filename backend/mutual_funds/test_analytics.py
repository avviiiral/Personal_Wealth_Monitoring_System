from decimal import Decimal
from types import SimpleNamespace

from django.test import SimpleTestCase

from mutual_funds.analytics import _transaction_cashflow
from mutual_funds.models import MutualFundTransactionType


class MutualFundPortfolioCashflowTests(SimpleTestCase):
    def test_purchase_and_sip_are_negative_including_fees(self):
        for transaction_type in (MutualFundTransactionType.PURCHASE, MutualFundTransactionType.SIP):
            transaction = SimpleNamespace(
                transaction_type=transaction_type,
                amount=Decimal("1000.00"),
                fees=Decimal("5.00"),
            )
            self.assertEqual(
                _transaction_cashflow(transaction),
                (Decimal("-1005.00"), "purchase"),
            )

    def test_redemption_and_dividend_are_positive_net_of_fees(self):
        redemption = SimpleNamespace(
            transaction_type=MutualFundTransactionType.REDEMPTION,
            amount=Decimal("600.00"),
            fees=Decimal("2.00"),
        )
        dividend = SimpleNamespace(
            transaction_type=MutualFundTransactionType.DIVIDEND,
            amount=Decimal("25.00"),
            fees=Decimal("0.00"),
        )
        self.assertEqual(
            _transaction_cashflow(redemption),
            (Decimal("598.00"), "redemption"),
        )
        self.assertEqual(
            _transaction_cashflow(dividend),
            (Decimal("25.00"), "dividend"),
        )

    def test_unsupported_transaction_type_is_excluded_from_xirr(self):
        transaction = SimpleNamespace(
            transaction_type="UNSUPPORTED",
            amount=Decimal("500.00"),
            fees=Decimal("0.00"),
        )
        self.assertEqual(_transaction_cashflow(transaction), (None, None))
