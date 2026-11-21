from decimal import Decimal
from types import SimpleNamespace

from django.test import SimpleTestCase

from investments.models import TransactionType as InvestmentTransactionType
from mutual_funds.analytics import _legacy_transaction_cashflow, _transaction_cashflow
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



class LegacyMutualFundCashflowTests(SimpleTestCase):
    def test_buy_and_sip_are_negative_outflows_including_fees(self):
        for transaction_type in (InvestmentTransactionType.BUY, InvestmentTransactionType.SIP):
            transaction = SimpleNamespace(
                transaction_type=transaction_type,
                amount=Decimal("1000.00"),
                fees=Decimal("5.00"),
            )
            self.assertEqual(
                _legacy_transaction_cashflow(transaction),
                (Decimal("-1005.00"), "purchase"),
            )

    def test_sell_and_dividend_are_positive_net_inflows(self):
        sell = SimpleNamespace(
            transaction_type=InvestmentTransactionType.SELL,
            amount=Decimal("600.00"),
            fees=Decimal("2.00"),
        )
        dividend = SimpleNamespace(
            transaction_type=InvestmentTransactionType.DIVIDEND,
            amount=Decimal("25.00"),
            fees=Decimal("0.00"),
        )
        self.assertEqual(
            _legacy_transaction_cashflow(sell),
            (Decimal("598.00"), "redemption"),
        )
        self.assertEqual(
            _legacy_transaction_cashflow(dividend),
            (Decimal("25.00"), "dividend"),
        )

    def test_non_cash_position_adjustments_are_not_xirr_flows(self):
        transaction = SimpleNamespace(
            transaction_type=InvestmentTransactionType.BONUS,
            amount=Decimal("500.00"),
            fees=Decimal("0.00"),
        )
        self.assertEqual(_legacy_transaction_cashflow(transaction), (None, None))


class PositiveNAVQueryRegressionTests(SimpleTestCase):
    def test_analytics_latest_nav_queries_require_positive_nav(self):
        # Regression guard: analytics must use the latest positive NAV,
        # rather than allowing a zero NAV to become the market valuation.
        from pathlib import Path

        analytics_source = (
            Path(__file__).parent / "analytics.py"
        ).read_text(encoding="utf-8")
        nav_service_source = (
            Path(__file__).parent / "services" / "nav_service.py"
        ).read_text(encoding="utf-8")
        amfi_source = (
            Path(__file__).parent / "services" / "amfi.py"
        ).read_text(encoding="utf-8")

        self.assertIn("scheme_id=OuterRef(\"scheme_id\"),\n        nav__gt=0,", analytics_source)
        self.assertIn("scheme_id=OuterRef(\"pk\"),\n            nav__gt=0,", analytics_source)
        self.assertIn("date__lte=target_date, nav__gt=0", nav_service_source)
        self.assertGreaterEqual(amfi_source.count("nav__gt=0"), 2)
