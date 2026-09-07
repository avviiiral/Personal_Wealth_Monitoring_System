from datetime import date
from decimal import Decimal

from django.db.models import Sum

from investments.models import (
    Holding,
    Transaction,
    TransactionType,
)

from mutual_funds.models import (
    MutualFundHolding,
    MutualFundTransaction,
    MutualFundTransactionType,
)

from .xirr import XIRRCalculator


class UnifiedWealthAnalytics:
    """
    Unified wealth analytics across all supported investment types.

    Currently combines:

        - Equities / investments
        - Mutual funds

    This service intentionally does not modify or replace the
    existing investment or mutual-fund holding engines.

    It reads their calculated holdings and aggregates them into
    one wealth view for PWMS.
    """

    ZERO = Decimal("0")

    @staticmethod
    def _owner_ids(user):
        """
        Normalize `user` to a list of owner ids to filter by.

        Accepts either a single User instance (existing,
        single-owner behavior - unchanged) or an iterable of user
        ids, for combining data across a shared-visibility group
        (see users.permissions.get_visible_owner_ids).
        """

        return [user.pk] if hasattr(user, "pk") else list(user)

    # ==========================================================
    # EQUITY
    # ==========================================================

    @staticmethod
    def get_equity_holdings(user):
        """
        Return active equity/investment holdings for the user.
        """

        return (
            Holding.objects
            .filter(
                owner_id__in=UnifiedWealthAnalytics._owner_ids(user),
                asset__is_active=True,
            )
            .select_related("asset")
        )

    @staticmethod
    def get_equity_totals(user):
        """
        Calculate total equity invested/current value/unrealized P&L.
        """

        result = (
            UnifiedWealthAnalytics
            .get_equity_holdings(user)
            .aggregate(
                invested=Sum("invested_value"),
                current=Sum("current_value"),
                unrealized=Sum("unrealized_pnl"),
            )
        )

        invested = (
            result["invested"]
            or UnifiedWealthAnalytics.ZERO
        )

        current = (
            result["current"]
            or UnifiedWealthAnalytics.ZERO
        )

        unrealized = (
            result["unrealized"]
            or UnifiedWealthAnalytics.ZERO
        )

        return {
            "invested": invested,
            "current": current,
            "unrealized": unrealized,
        }

    @staticmethod
    def calculate_equity_realized_pnl(user, family_name=None):
        """
        Calculate realized equity P&L using average-cost methodology.

        BUY/SIP:
            Increase quantity and cost basis.

        SELL:
            Remove average cost of the sold quantity and calculate
            realized P&L.

        family_name:
            Optional. When provided, scopes the calculation to that
            exact Family Name only. Leaving it unset preserves the
            original all-families calculation exactly.
        """

        transactions_qs = (
            Transaction.objects
            .filter(owner_id__in=UnifiedWealthAnalytics._owner_ids(user))
        )

        if family_name:
            transactions_qs = (
                transactions_qs
                .filter(family_name=family_name)
            )

        transactions = (
            transactions_qs
            .order_by(
                "asset_id",
                "transaction_date",
                "created_at",
                "id",
            )
        )

        positions = {}
        realized_pnl = UnifiedWealthAnalytics.ZERO

        for transaction in transactions:

            asset_id = transaction.asset_id

            if asset_id not in positions:
                positions[asset_id] = {
                    "quantity": UnifiedWealthAnalytics.ZERO,
                    "invested_value": UnifiedWealthAnalytics.ZERO,
                }

            position = positions[asset_id]

            quantity = (
                transaction.quantity
                or UnifiedWealthAnalytics.ZERO
            )

            amount = (
                transaction.amount
                or UnifiedWealthAnalytics.ZERO
            )

            fees = (
                transaction.fees
                or UnifiedWealthAnalytics.ZERO
            )

            if transaction.transaction_type in (
                TransactionType.BUY,
                TransactionType.SIP,
            ):
                position["quantity"] += quantity

                # Excludes fees, matching
                # HoldingCalculationEngine.calculate_position() -
                # see historical_wealth.py's
                # _apply_equity_transaction for the full rationale.
                # Sale proceeds below still subtract fees since
                # that's a different concept (cash actually
                # received), not invested value.
                position["invested_value"] += amount

            elif transaction.transaction_type == TransactionType.SELL:

                if (
                    position["quantity"] <= 0
                    or quantity <= 0
                ):
                    continue

                average_cost = (
                    position["invested_value"]
                    / position["quantity"]
                )

                cost_of_sale = (
                    average_cost * quantity
                )

                sale_proceeds = amount - fees

                realized_pnl += (
                    sale_proceeds
                    - cost_of_sale
                )

                position["quantity"] -= quantity
                position["invested_value"] -= cost_of_sale

                if position["quantity"] <= 0:
                    position["quantity"] = (
                        UnifiedWealthAnalytics.ZERO
                    )
                    position["invested_value"] = (
                        UnifiedWealthAnalytics.ZERO
                    )

        return realized_pnl

    # ==========================================================
    # MUTUAL FUNDS
    # ==========================================================

    @staticmethod
    def get_mutual_fund_holdings(user):
        """
        Return active mutual-fund holdings for the user.
        """

        return (
            MutualFundHolding.objects
            .filter(
                owner_id__in=UnifiedWealthAnalytics._owner_ids(user),
                scheme__is_active=True,
            )
            .select_related("scheme")
        )

    @staticmethod
    def get_mutual_fund_totals(user):
        """
        Calculate total mutual-fund invested/current value/unrealized P&L.
        """

        result = (
            UnifiedWealthAnalytics
            .get_mutual_fund_holdings(user)
            .aggregate(
                invested=Sum("invested_value"),
                current=Sum("current_value"),
                unrealized=Sum("unrealized_pnl"),
            )
        )

        invested = (
            result["invested"]
            or UnifiedWealthAnalytics.ZERO
        )

        current = (
            result["current"]
            or UnifiedWealthAnalytics.ZERO
        )

        unrealized = (
            result["unrealized"]
            or UnifiedWealthAnalytics.ZERO
        )

        return {
            "invested": invested,
            "current": current,
            "unrealized": unrealized,
        }

    @staticmethod
    def calculate_mutual_fund_realized_pnl(user, family_name=None):
        """
        Calculate realized mutual-fund P&L using average-cost methodology.

        PURCHASE/SIP:
            Increase units and cost basis.

        REDEMPTION:
            Remove average cost of redeemed units and calculate
            realized P&L.

        family_name:
            Optional. When provided, scopes the calculation to that
            exact Family Name only. Leaving it unset preserves the
            original all-families calculation exactly.
        """

        transactions_qs = (
            MutualFundTransaction.objects
            .filter(owner_id__in=UnifiedWealthAnalytics._owner_ids(user))
        )

        if family_name:
            transactions_qs = (
                transactions_qs
                .filter(family_name=family_name)
            )

        transactions = (
            transactions_qs
            .order_by(
                "scheme_id",
                "transaction_date",
                "created_at",
                "id",
            )
        )

        positions = {}
        realized_pnl = UnifiedWealthAnalytics.ZERO

        for transaction in transactions:

            scheme_id = transaction.scheme_id

            if scheme_id not in positions:
                positions[scheme_id] = {
                    "units": UnifiedWealthAnalytics.ZERO,
                    "invested_value": UnifiedWealthAnalytics.ZERO,
                }

            position = positions[scheme_id]

            units = (
                transaction.units
                or UnifiedWealthAnalytics.ZERO
            )

            amount = (
                transaction.amount
                or UnifiedWealthAnalytics.ZERO
            )

            fees = (
                transaction.fees
                or UnifiedWealthAnalytics.ZERO
            )

            if transaction.transaction_type in (
                MutualFundTransactionType.PURCHASE,
                MutualFundTransactionType.SIP,
            ):

                position["units"] += units

                # Excludes fees - see calculate_equity_realized_pnl
                # above for the full rationale.
                position["invested_value"] += amount

            elif transaction.transaction_type == (
                MutualFundTransactionType.REDEMPTION
            ):

                if (
                    position["units"] <= 0
                    or units <= 0
                ):
                    continue

                average_cost = (
                    position["invested_value"]
                    / position["units"]
                )

                cost_of_redemption = (
                    average_cost * units
                )

                redemption_proceeds = (
                    amount - fees
                )

                realized_pnl += (
                    redemption_proceeds
                    - cost_of_redemption
                )

                position["units"] -= units
                position["invested_value"] -= (
                    cost_of_redemption
                )

                if position["units"] <= 0:
                    position["units"] = (
                        UnifiedWealthAnalytics.ZERO
                    )
                    position["invested_value"] = (
                        UnifiedWealthAnalytics.ZERO
                    )

        return realized_pnl

    # ==========================================================
    # UNIFIED SUMMARY
    # ==========================================================

    @staticmethod
    def _count_family_positions(user, family_name):
        """
        Count open (quantity/units > 0) equity assets and mutual-fund
        schemes for one exact Family Name.

        Holding/MutualFundHolding have no family_name field (they are
        one aggregated row per asset/scheme across ALL families), so
        family-scoped counts must be rebuilt from the transactions
        directly - reusing the exact same average-cost position
        methods HistoricalWealthAnalytics already uses for the
        Wealth Overview chart, so the math stays identical to the
        rest of the family-filtered Dashboard.
        """

        from .historical_wealth import HistoricalWealthAnalytics

        equity_positions = {}

        equity_transactions = (
            Transaction.objects
            .filter(
                owner_id__in=UnifiedWealthAnalytics._owner_ids(user),
                family_name=family_name,
            )
            .order_by(
                "asset_id",
                "transaction_date",
                "created_at",
                "id",
            )
        )

        for transaction in equity_transactions:
            position = equity_positions.setdefault(
                transaction.asset_id,
                {
                    "quantity": UnifiedWealthAnalytics.ZERO,
                    "invested_value": UnifiedWealthAnalytics.ZERO,
                },
            )

            HistoricalWealthAnalytics._apply_equity_transaction(
                position,
                transaction,
            )

        equity_count = sum(
            1
            for position in equity_positions.values()
            if position["quantity"] > 0
        )

        mutual_fund_positions = {}

        mutual_fund_transactions = (
            MutualFundTransaction.objects
            .filter(
                owner_id__in=UnifiedWealthAnalytics._owner_ids(user),
                family_name=family_name,
            )
            .order_by(
                "scheme_id",
                "transaction_date",
                "created_at",
                "id",
            )
        )

        for transaction in mutual_fund_transactions:
            position = mutual_fund_positions.setdefault(
                transaction.scheme_id,
                {
                    "units": UnifiedWealthAnalytics.ZERO,
                    "invested_value": UnifiedWealthAnalytics.ZERO,
                },
            )

            HistoricalWealthAnalytics._apply_mutual_fund_transaction(
                position,
                transaction,
            )

        mutual_fund_count = sum(
            1
            for position in mutual_fund_positions.values()
            if position["units"] > 0
        )

        return equity_count, mutual_fund_count

    @staticmethod
    def calculate_summary(user, family_name=None):
        """
        Calculate the complete unified wealth summary.

        family_name:
            Optional. When omitted, this is byte-for-byte the
            original all-families calculation (Holding /
            MutualFundHolding based) - unchanged.

            When provided, Holding/MutualFundHolding cannot be used
            because neither carries a family_name (each is a single
            aggregated row per asset/scheme across every family), so
            this path instead sources today's invested/current/P&L
            from HistoricalWealthAnalytics.calculate_history() for
            just that one day - the same family-aware, transaction-
            based calculation already powering the Wealth Overview
            chart - so the KPI cards and the chart always agree.
        """

        if not family_name:
            equity = (
                UnifiedWealthAnalytics
                .get_equity_totals(user)
            )

            mutual_funds = (
                UnifiedWealthAnalytics
                .get_mutual_fund_totals(user)
            )

            equity_realized = (
                UnifiedWealthAnalytics
                .calculate_equity_realized_pnl(user)
            )

            mutual_fund_realized = (
                UnifiedWealthAnalytics
                .calculate_mutual_fund_realized_pnl(user)
            )

            total_invested = (
                equity["invested"]
                + mutual_funds["invested"]
            )

            total_current_value = (
                equity["current"]
                + mutual_funds["current"]
            )

            unrealized_pnl = (
                equity["unrealized"]
                + mutual_funds["unrealized"]
            )

            realized_pnl = (
                equity_realized
                + mutual_fund_realized
            )

            total_pnl = (
                realized_pnl
                + unrealized_pnl
            )

            return_percentage = (
                (
                    total_pnl
                    / total_invested
                ) * 100
                if total_invested
                else UnifiedWealthAnalytics.ZERO
            )

            xirr_percentage = (
                UnifiedWealthAnalytics
                .calculate_xirr(user)
            )

            equity_count = (
                UnifiedWealthAnalytics
                .get_equity_holdings(user)
                .count()
            )

            mutual_fund_count = (
                UnifiedWealthAnalytics
                .get_mutual_fund_holdings(user)
                .count()
            )

            return {
                "total_invested": total_invested,
                "total_current_value": total_current_value,
                "realized_pnl": realized_pnl,
                "unrealized_pnl": unrealized_pnl,
                "total_pnl": total_pnl,
                "return_percentage": round(
                    return_percentage,
                    2,
                ),
                "xirr_percentage": xirr_percentage,
                "number_of_holdings": (
                    equity_count
                    + mutual_fund_count
                ),
                "equity": {
                    "invested": equity["invested"],
                    "current_value": equity["current"],
                    "unrealized_pnl": equity["unrealized"],
                    "realized_pnl": equity_realized,
                    "number_of_holdings": equity_count,
                },
                "mutual_funds": {
                    "invested": mutual_funds["invested"],
                    "current_value": mutual_funds["current"],
                    "unrealized_pnl": mutual_funds["unrealized"],
                    "realized_pnl": mutual_fund_realized,
                    "number_of_holdings": mutual_fund_count,
                },
            }

        # ==================================================
        # FAMILY-FILTERED PATH
        # ==================================================

        from datetime import date

        from .historical_wealth import HistoricalWealthAnalytics

        today = date.today()

        today_rows = (
            HistoricalWealthAnalytics
            .calculate_history(
                user,
                today,
                today,
                family_name=family_name,
            )
        )

        today_totals = (
            today_rows[0]
            if today_rows
            else {
                "invested_value": UnifiedWealthAnalytics.ZERO,
                "portfolio_value": UnifiedWealthAnalytics.ZERO,
                "pnl": UnifiedWealthAnalytics.ZERO,
                "equity": {
                    "invested_value": UnifiedWealthAnalytics.ZERO,
                    "portfolio_value": UnifiedWealthAnalytics.ZERO,
                    "pnl": UnifiedWealthAnalytics.ZERO,
                },
                "mutual_funds": {
                    "invested_value": UnifiedWealthAnalytics.ZERO,
                    "portfolio_value": UnifiedWealthAnalytics.ZERO,
                    "pnl": UnifiedWealthAnalytics.ZERO,
                },
            }
        )

        total_invested = today_totals["invested_value"]
        total_current_value = today_totals["portfolio_value"]
        unrealized_pnl = today_totals["pnl"]

        equity_realized = (
            UnifiedWealthAnalytics
            .calculate_equity_realized_pnl(
                user,
                family_name=family_name,
            )
        )

        mutual_fund_realized = (
            UnifiedWealthAnalytics
            .calculate_mutual_fund_realized_pnl(
                user,
                family_name=family_name,
            )
        )

        realized_pnl = (
            equity_realized
            + mutual_fund_realized
        )

        total_pnl = (
            realized_pnl
            + unrealized_pnl
        )

        return_percentage = (
            (
                total_pnl
                / total_invested
            ) * 100
            if total_invested
            else UnifiedWealthAnalytics.ZERO
        )

        xirr_percentage = (
            UnifiedWealthAnalytics
            .calculate_xirr(
                user,
                family_name=family_name,
            )
        )

        equity_count, mutual_fund_count = (
            UnifiedWealthAnalytics
            ._count_family_positions(
                user,
                family_name,
            )
        )

        return {
            "total_invested": total_invested,
            "total_current_value": total_current_value,
            "realized_pnl": realized_pnl,
            "unrealized_pnl": unrealized_pnl,
            "total_pnl": total_pnl,
            "return_percentage": round(
                return_percentage,
                2,
            ),
            "xirr_percentage": xirr_percentage,
            "number_of_holdings": (
                equity_count
                + mutual_fund_count
            ),
            "equity": {
                "invested": (
                    today_totals["equity"]["invested_value"]
                ),
                "current_value": (
                    today_totals["equity"]["portfolio_value"]
                ),
                "unrealized_pnl": (
                    today_totals["equity"]["pnl"]
                ),
                "realized_pnl": equity_realized,
                "number_of_holdings": equity_count,
            },
            "mutual_funds": {
                "invested": (
                    today_totals["mutual_funds"]["invested_value"]
                ),
                "current_value": (
                    today_totals["mutual_funds"]["portfolio_value"]
                ),
                "unrealized_pnl": (
                    today_totals["mutual_funds"]["pnl"]
                ),
                "realized_pnl": mutual_fund_realized,
                "number_of_holdings": mutual_fund_count,
            },
        }

    # ==========================================================
    # XIRR
    # ==========================================================

    @staticmethod
    def calculate_xirr(user, family_name=None):
        """
        Calculate unified XIRR across equities and mutual funds.

        Investments are negative cash flows.

        Sales/redemptions/dividends/interest are positive cash flows.

        Current combined portfolio value is added as the terminal
        positive cash flow.

        family_name:
            Optional. When provided, scopes every cash flow to that
            exact Family Name, and the terminal "current value" cash
            flow is sourced from HistoricalWealthAnalytics (today's
            row for that family) instead of Holding/MutualFundHolding
            totals, since neither carries a family_name. Leaving it
            unset preserves the original all-families calculation
            exactly.
        """

        cash_flows = []

        equity_transactions_qs = (
            Transaction.objects
            .filter(owner_id__in=UnifiedWealthAnalytics._owner_ids(user))
        )

        if family_name:
            equity_transactions_qs = (
                equity_transactions_qs
                .filter(family_name=family_name)
            )

        equity_transactions = (
            equity_transactions_qs
            .order_by(
                "transaction_date",
                "created_at",
                "id",
            )
        )

        for transaction in equity_transactions:

            amount = (
                transaction.amount
                or UnifiedWealthAnalytics.ZERO
            )

            fees = (
                transaction.fees
                or UnifiedWealthAnalytics.ZERO
            )

            if transaction.transaction_type in (
                TransactionType.BUY,
                TransactionType.SIP,
                TransactionType.DEPOSIT,
            ):

                cash_flows.append(
                    (
                        transaction.transaction_date,
                        -(amount + fees),
                    )
                )

            elif transaction.transaction_type in (
                TransactionType.SELL,
                TransactionType.DIVIDEND,
                TransactionType.INTEREST,
                TransactionType.WITHDRAWAL,
            ):

                cash_flows.append(
                    (
                        transaction.transaction_date,
                        amount - fees,
                    )
                )

        mutual_fund_transactions_qs = (
            MutualFundTransaction.objects
            .filter(owner_id__in=UnifiedWealthAnalytics._owner_ids(user))
        )

        if family_name:
            mutual_fund_transactions_qs = (
                mutual_fund_transactions_qs
                .filter(family_name=family_name)
            )

        mutual_fund_transactions = (
            mutual_fund_transactions_qs
            .order_by(
                "transaction_date",
                "created_at",
                "id",
            )
        )

        for transaction in mutual_fund_transactions:

            amount = (
                transaction.amount
                or UnifiedWealthAnalytics.ZERO
            )

            fees = (
                transaction.fees
                or UnifiedWealthAnalytics.ZERO
            )

            if transaction.transaction_type in (
                MutualFundTransactionType.PURCHASE,
                MutualFundTransactionType.SIP,
            ):

                cash_flows.append(
                    (
                        transaction.transaction_date,
                        -(amount + fees),
                    )
                )

            elif transaction.transaction_type in (
                MutualFundTransactionType.REDEMPTION,
                MutualFundTransactionType.DIVIDEND,
            ):

                cash_flows.append(
                    (
                        transaction.transaction_date,
                        amount - fees,
                    )
                )

        if family_name:
            from .historical_wealth import (
                HistoricalWealthAnalytics,
            )

            today_rows = (
                HistoricalWealthAnalytics
                .calculate_history(
                    user,
                    date.today(),
                    date.today(),
                    family_name=family_name,
                )
            )

            current_value = (
                today_rows[0]["portfolio_value"]
                if today_rows
                else UnifiedWealthAnalytics.ZERO
            )
        else:
            equity_totals = (
                UnifiedWealthAnalytics
                .get_equity_totals(user)
            )

            mutual_fund_totals = (
                UnifiedWealthAnalytics
                .get_mutual_fund_totals(user)
            )

            current_value = (
                equity_totals["current"]
                + mutual_fund_totals["current"]
            )

        if current_value > 0:
            cash_flows.append(
                (
                    date.today(),
                    current_value,
                )
            )

        result = XIRRCalculator.calculate(
            cash_flows
        )

        if result is None:
            return None

        return round(
            result * 100,
            2,
        )

    # ==========================================================
    # ALLOCATION
    # ==========================================================

    @staticmethod
    def calculate_allocation(user):
        """
        Calculate unified allocation by top-level asset class.
        """

        equity_holdings = (
            UnifiedWealthAnalytics
            .get_equity_holdings(user)
        )

        mutual_fund_holdings = (
            UnifiedWealthAnalytics
            .get_mutual_fund_holdings(user)
        )

        allocation = {}

        for holding in equity_holdings:

            category = holding.asset.category

            value = (
                holding.current_value
                or UnifiedWealthAnalytics.ZERO
            )

            if category not in allocation:
                allocation[category] = (
                    UnifiedWealthAnalytics.ZERO
                )

            allocation[category] += value

        mutual_fund_value = sum(
            (
                holding.current_value
                or UnifiedWealthAnalytics.ZERO
            )
            for holding in mutual_fund_holdings
        )

        if mutual_fund_value:
            allocation["MUTUAL_FUND"] = (
                allocation.get(
                    "MUTUAL_FUND",
                    UnifiedWealthAnalytics.ZERO,
                )
                + mutual_fund_value
            )

        total_value = sum(
            allocation.values(),
            UnifiedWealthAnalytics.ZERO,
        )

        results = []

        for category, value in allocation.items():

            percentage = (
                (
                    value / total_value
                ) * 100
                if total_value
                else UnifiedWealthAnalytics.ZERO
            )

            results.append({
                "category": category,
                "value": value,
                "percentage": round(
                    percentage,
                    2,
                ),
            })

        return sorted(
            results,
            key=lambda item: item["value"],
            reverse=True,
        )

    # ==========================================================
    # PERFORMANCE
    # ==========================================================

    @staticmethod
    def calculate_performance(user):
        """
        Return unified performance ranking across equities
        and mutual funds.
        """

        results = []

        equity_holdings = (
            UnifiedWealthAnalytics
            .get_equity_holdings(user)
        )

        for holding in equity_holdings:

            invested = (
                holding.invested_value
                or UnifiedWealthAnalytics.ZERO
            )

            current_value = (
                holding.current_value
                or UnifiedWealthAnalytics.ZERO
            )

            pnl = (
                holding.unrealized_pnl
                or UnifiedWealthAnalytics.ZERO
            )

            percentage = (
                (pnl / invested) * 100
                if invested
                else UnifiedWealthAnalytics.ZERO
            )

            results.append({
                "type": "EQUITY",
                "holding_id": holding.pk,
                "name": holding.asset.name,
                "symbol": holding.asset.symbol,
                "invested_value": invested,
                "current_value": current_value,
                "unrealized_pnl": pnl,
                "pnl_percentage": round(
                    percentage,
                    2,
                ),
            })

        mutual_fund_holdings = (
            UnifiedWealthAnalytics
            .get_mutual_fund_holdings(user)
        )

        for holding in mutual_fund_holdings:

            invested = (
                holding.invested_value
                or UnifiedWealthAnalytics.ZERO
            )

            current_value = (
                holding.current_value
                or UnifiedWealthAnalytics.ZERO
            )

            pnl = (
                holding.unrealized_pnl
                or UnifiedWealthAnalytics.ZERO
            )

            percentage = (
                (pnl / invested) * 100
                if invested
                else UnifiedWealthAnalytics.ZERO
            )

            results.append({
                "type": "MUTUAL_FUND",
                "holding_id": holding.pk,
                "name": holding.scheme.scheme_name,
                "symbol": holding.scheme.scheme_code,
                "invested_value": invested,
                "current_value": current_value,
                "unrealized_pnl": pnl,
                "pnl_percentage": round(
                    percentage,
                    2,
                ),
            })

        return sorted(
            results,
            key=lambda item: item["pnl_percentage"],
            reverse=True,
        )