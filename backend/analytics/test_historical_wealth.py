from django.test import TestCase
from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase

from investments.models import (
    Asset,
    AssetCategory,
    Transaction,
    TransactionType,
)
from market_data.models import (
    DataSource,
    MarketPrice,
)
from mutual_funds.models import (
    MutualFundNAV,
    MutualFundScheme,
    MutualFundTransaction,
    MutualFundTransactionType,
)

from .services.historical_wealth import (
    HistoricalWealthAnalytics,
)


class HistoricalWealthAnalyticsTests(TestCase):
    """
    Tests unified historical wealth calculations for:

    - Equities
    - Mutual funds
    - Combined portfolio
    """

    def setUp(self):
        self.user = User.objects.create_user(
            username="historical_test_user",
            password="testpassword123",
        )

    # ==========================================================
    # EQUITY FIXTURES
    # ==========================================================

    def create_equity(self):
        asset = Asset.objects.create(
            owner=self.user,
            name="Historical Test Stock",
            category=AssetCategory.STOCK,
            symbol="HTEST",
            currency="INR",
            is_active=True,
        )

        Transaction.objects.create(
            owner=self.user,
            asset=asset,
            transaction_type=TransactionType.BUY,
            transaction_date=date(2026, 1, 1),
            quantity=Decimal("10"),
            price_per_unit=Decimal("100"),
            amount=Decimal("1000"),
            fees=Decimal("10"),
        )

        MarketPrice.objects.create(
            asset=asset,
            date=date(2026, 1, 1),
            open_price=Decimal("100"),
            high_price=Decimal("100"),
            low_price=Decimal("100"),
            close_price=Decimal("100"),
            adjusted_close=Decimal("100"),
            source=DataSource.YAHOO_FINANCE,
        )

        MarketPrice.objects.create(
            asset=asset,
            date=date(2026, 1, 2),
            open_price=Decimal("120"),
            high_price=Decimal("120"),
            low_price=Decimal("120"),
            close_price=Decimal("120"),
            adjusted_close=Decimal("120"),
            source=DataSource.YAHOO_FINANCE,
        )

        return asset

    # ==========================================================
    # MUTUAL FUND FIXTURES
    # ==========================================================

    def create_mutual_fund(self):
        scheme = MutualFundScheme.objects.create(
            owner=self.user,
            scheme_name="Historical Test Fund",
            amc_name="Test AMC",
            scheme_code="HTESTMF",
            category="Equity",
            plan="Direct",
            option="Growth",
            is_active=True,
        )

        MutualFundTransaction.objects.create(
            owner=self.user,
            scheme=scheme,
            transaction_type=(
                MutualFundTransactionType.PURCHASE
            ),
            transaction_date=date(2026, 1, 1),
            units=Decimal("100"),
            nav=Decimal("100"),
            amount=Decimal("10000"),
            fees=Decimal("0"),
        )

        MutualFundNAV.objects.create(
            scheme=scheme,
            date=date(2026, 1, 1),
            nav=Decimal("100"),
            source="AMFI",
        )

        MutualFundNAV.objects.create(
            scheme=scheme,
            date=date(2026, 1, 2),
            nav=Decimal("110"),
            source="AMFI",
        )

        return scheme

    # ==========================================================
    # EQUITY HISTORICAL VALUE
    # ==========================================================

    def test_equity_historical_value(self):
        self.create_equity()

        result = (
            HistoricalWealthAnalytics
            .calculate_historical_value(
                self.user,
                date(2026, 1, 1),
            )
        )

        # Invested value excludes fees (matches
        # HoldingCalculationEngine.calculate_position(), the engine
        # behind Holding.invested_value) - the fixture's BUY is
        # amount=1000, fees=10, so invested_value is 1000, not 1010.
        self.assertEqual(
            result["equity"]["invested_value"],
            Decimal("1000"),
        )

        self.assertEqual(
            result["equity"]["portfolio_value"],
            Decimal("1000"),
        )

        self.assertEqual(
            result["equity"]["pnl"],
            Decimal("0"),
        )

    def test_equity_historical_value_uses_latest_price(self):
        self.create_equity()

        result = (
            HistoricalWealthAnalytics
            .calculate_historical_value(
                self.user,
                date(2026, 1, 3),
            )
        )

        self.assertEqual(
            result["equity"]["portfolio_value"],
            Decimal("1200"),
        )

    # ==========================================================
    # MUTUAL FUND HISTORICAL VALUE
    # ==========================================================

    def test_mutual_fund_historical_value(self):
        self.create_mutual_fund()

        result = (
            HistoricalWealthAnalytics
            .calculate_historical_value(
                self.user,
                date(2026, 1, 1),
            )
        )

        self.assertEqual(
            result["mutual_funds"]["invested_value"],
            Decimal("10000"),
        )

        self.assertEqual(
            result["mutual_funds"]["portfolio_value"],
            Decimal("10000"),
        )

        self.assertEqual(
            result["mutual_funds"]["pnl"],
            Decimal("0"),
        )

    def test_mutual_fund_historical_value_uses_latest_nav(self):
        self.create_mutual_fund()

        result = (
            HistoricalWealthAnalytics
            .calculate_historical_value(
                self.user,
                date(2026, 1, 3),
            )
        )

        self.assertEqual(
            result["mutual_funds"]["portfolio_value"],
            Decimal("11000"),
        )

    # ==========================================================
    # UNIFIED VALUE
    # ==========================================================

    def test_unified_historical_value(self):
        self.create_equity()
        self.create_mutual_fund()

        result = (
            HistoricalWealthAnalytics
            .calculate_historical_value(
                self.user,
                date(2026, 1, 2),
            )
        )

        # Invested value excludes fees - equity fixture is
        # amount=1000, fees=10, so total invested is 1000+10000=
        # 11000, not 11010 (mutual fund fixture has fees=0).
        self.assertEqual(
            result["invested_value"],
            Decimal("11000"),
        )

        self.assertEqual(
            result["portfolio_value"],
            Decimal("12200"),
        )

        self.assertEqual(
            result["pnl"],
            Decimal("1200"),
        )

    # ==========================================================
    # HISTORY RANGE
    # ==========================================================

    def test_history_returns_each_calendar_day(self):
        self.create_equity()
        self.create_mutual_fund()

        results = (
            HistoricalWealthAnalytics
            .calculate_history(
                self.user,
                date(2026, 1, 1),
                date(2026, 1, 3),
            )
        )

        self.assertEqual(
            len(results),
            3,
        )

        self.assertEqual(
            results[0]["date"],
            date(2026, 1, 1),
        )

        self.assertEqual(
            results[1]["date"],
            date(2026, 1, 2),
        )

        self.assertEqual(
            results[2]["date"],
            date(2026, 1, 3),
        )

    # ==========================================================
    # PERFORMANCE
    # ==========================================================

    def test_historical_calculation_does_not_scan_all_mf_schemes(
        self
    ):
        self.create_equity()
        self.create_mutual_fund()

        result = (
            HistoricalWealthAnalytics
            .calculate_historical_value(
                self.user,
                date(2026, 1, 2),
            )
        )

        self.assertEqual(
            result["portfolio_value"],
            Decimal("12200"),
        )
        
    # ==========================================================
    # USER ISOLATION
    # ==========================================================

    def test_other_users_are_not_included(self):
        self.create_equity()
        self.create_mutual_fund()

        other_user = User.objects.create_user(
            username="historical_other_user",
            password="testpassword123",
        )

        result = (
            HistoricalWealthAnalytics
            .calculate_historical_value(
                other_user,
                date(2026, 1, 2),
            )
        )

        self.assertEqual(
            result["invested_value"],
            Decimal("0"),
        )

        self.assertEqual(
            result["portfolio_value"],
            Decimal("0"),
        )

        self.assertEqual(
            result["pnl"],
            Decimal("0"),
        )

    # ==========================================================
    # INVALID DATE RANGE
    # ==========================================================

    def test_invalid_date_range_raises_error(self):
        with self.assertRaises(ValueError):
            HistoricalWealthAnalytics.calculate_history(
                self.user,
                date(2026, 1, 10),
                date(2026, 1, 1),
            )

    # ==========================================================
    # NO DATA
    # ==========================================================

    def test_empty_user_returns_zero(self):
        result = (
            HistoricalWealthAnalytics
            .calculate_historical_value(
                self.user,
                date(2026, 1, 1),
            )
        )

        self.assertEqual(
            result["invested_value"],
            Decimal("0"),
        )

        self.assertEqual(
            result["portfolio_value"],
            Decimal("0"),
        )

        self.assertEqual(
            result["pnl"],
            Decimal("0"),
        )