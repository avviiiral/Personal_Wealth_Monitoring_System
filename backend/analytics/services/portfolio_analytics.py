from datetime import date
from decimal import Decimal

from django.db.models import Sum, Q

from investments.models import (
    Asset,
    Holding,
    Transaction,
    TransactionType,
)
from market_data.models import MarketPrice

from .xirr import XIRRCalculator
from users.permissions import get_active_family_group, is_system_owner


class PortfolioAnalytics:

    ZERO = Decimal("0")

    @staticmethod
    def _scope_q(user):
        if is_system_owner(user):
            return Q()
        family = get_active_family_group(user)
        if family is None:
            return Q(pk__in=[])
        return Q(family_id=family.id)

    @staticmethod
    def calculate_xirr(user):
        transactions = (
            Transaction.objects
            .filter(PortfolioAnalytics._scope_q(user))
            .order_by(
                "transaction_date",
                "created_at",
                "id",
            )
        )

        cash_flows = []

        for tx in transactions:
            amount = tx.amount or PortfolioAnalytics.ZERO
            fees = tx.fees or PortfolioAnalytics.ZERO

            if tx.transaction_type in (
                TransactionType.BUY,
                TransactionType.SIP,
                TransactionType.DEPOSIT,
            ):
                cash_flows.append(
                    (
                        tx.transaction_date,
                        -(amount + fees),
                    )
                )

            elif tx.transaction_type in (
                TransactionType.SELL,
                TransactionType.DIVIDEND,
                TransactionType.INTEREST,
                TransactionType.WITHDRAWAL,
            ):
                cash_flows.append(
                    (
                        tx.transaction_date,
                        amount - fees,
                    )
                )

        holdings = PortfolioAnalytics.get_holdings(user)

        current_value = sum(
            (
                holding.current_value
                or PortfolioAnalytics.ZERO
            )
            for holding in holdings
        )

        if current_value > 0:
            cash_flows.append(
                (
                    date.today(),
                    current_value,
                )
            )

        xirr = XIRRCalculator.calculate(cash_flows)

        if xirr is None:
            return None

        return round(xirr * 100, 2)

    @staticmethod
    def get_holdings(user):
        return (
            Holding.objects
            .filter(
                PortfolioAnalytics._scope_q(user),
                asset__is_active=True,
            )
            .select_related("asset")
        )

    @staticmethod
    def calculate_unrealized_pnl(user):
        result = (
            PortfolioAnalytics
            .get_holdings(user)
            .aggregate(
                invested=Sum("invested_value"),
                current=Sum("current_value"),
            )
        )

        invested = (
            result["invested"]
            or PortfolioAnalytics.ZERO
        )

        current = (
            result["current"]
            or PortfolioAnalytics.ZERO
        )

        return current - invested

    @staticmethod
    def calculate_realized_pnl(user):
        transactions = (
            Transaction.objects
            .filter(PortfolioAnalytics._scope_q(user))
            .select_related("asset")
            .order_by(
                "asset_id",
                "transaction_date",
                "created_at",
                "id",
            )
        )

        positions = {}
        realized_pnl = PortfolioAnalytics.ZERO

        for tx in transactions:
            asset_id = tx.asset.id

            if asset_id not in positions:
                positions[asset_id] = {
                    "quantity": PortfolioAnalytics.ZERO,
                    "invested_value": PortfolioAnalytics.ZERO,
                }

            position = positions[asset_id]

            quantity = (
                tx.quantity
                or PortfolioAnalytics.ZERO
            )

            amount = (
                tx.amount
                or PortfolioAnalytics.ZERO
            )

            fees = (
                tx.fees
                or PortfolioAnalytics.ZERO
            )

            if tx.transaction_type in (
                TransactionType.BUY,
                TransactionType.SIP,
            ):
                position["quantity"] += quantity
                position["invested_value"] += (
                    amount + fees
                )

            elif tx.transaction_type == TransactionType.SELL:
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

                realized_pnl += (
                    amount
                    - fees
                    - cost_of_sale
                )

                position["quantity"] -= quantity
                position["invested_value"] -= cost_of_sale

                if position["quantity"] <= 0:
                    position["quantity"] = (
                        PortfolioAnalytics.ZERO
                    )
                    position["invested_value"] = (
                        PortfolioAnalytics.ZERO
                    )

        return realized_pnl

    @staticmethod
    def calculate_summary(user):
        holdings = PortfolioAnalytics.get_holdings(user)

        totals = holdings.aggregate(
            invested=Sum("invested_value"),
            current=Sum("current_value"),
            unrealized=Sum("unrealized_pnl"),
        )

        total_invested = (
            totals["invested"]
            or PortfolioAnalytics.ZERO
        )

        total_current_value = (
            totals["current"]
            or PortfolioAnalytics.ZERO
        )

        unrealized_pnl = (
            totals["unrealized"]
            or PortfolioAnalytics.ZERO
        )

        realized_pnl = (
            PortfolioAnalytics
            .calculate_realized_pnl(user)
        )

        total_pnl = (
            realized_pnl
            + unrealized_pnl
        )

        return_percentage = (
            (total_pnl / total_invested) * 100
            if total_invested
            else PortfolioAnalytics.ZERO
        )

        xirr = PortfolioAnalytics.calculate_xirr(user)

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
            "xirr_percentage": xirr,
            "number_of_holdings": holdings.count(),
        }

    @staticmethod
    def calculate_allocation(user):
        # Aggregate allocation in SQL instead of loading every holding into
        # Python. This keeps the result identical while reducing application
        # work for large portfolios.
        rows = (
            PortfolioAnalytics
            .get_holdings(user)
            .values("asset__category")
            .annotate(value=Sum("current_value"))
        )

        total_value = PortfolioAnalytics.ZERO
        allocation = []

        for row in rows:
            value = row["value"] or PortfolioAnalytics.ZERO
            total_value += value
            allocation.append(
                {
                    "category": row["asset__category"],
                    "value": value,
                    "percentage": 0,
                }
            )

        for item in allocation:
            value = item["value"]
            percentage = (
                (value / total_value) * 100
                if total_value
                else PortfolioAnalytics.ZERO
            )
            item["percentage"] = round(percentage, 2)

        return allocation

    @staticmethod
    def get_performance_ranking(user):
        holdings = list(
            PortfolioAnalytics.get_holdings(user)
        )

        results = []

        for holding in holdings:
            if holding.invested_value:
                pnl_percentage = (
                    holding.unrealized_pnl
                    / holding.invested_value
                ) * 100
            else:
                pnl_percentage = PortfolioAnalytics.ZERO

            results.append(
                {
                    "asset_id": holding.asset.id,
                    "asset_name": holding.asset.name,
                    "symbol": holding.asset.symbol,
                    "current_value": holding.current_value,
                    "unrealized_pnl": holding.unrealized_pnl,
                    "pnl_percentage": round(
                        pnl_percentage,
                        2,
                    ),
                }
            )

        return sorted(
            results,
            key=lambda item: item["pnl_percentage"],
            reverse=True,
        )

    @staticmethod
    def calculate_position_as_of(
        asset,
        target_date,
    ):
        transactions = (
            Transaction.objects
            .filter(
                asset=asset,
                family=asset.family,
                transaction_date__lte=target_date,
            )
            .order_by(
                "transaction_date",
                "created_at",
                "id",
            )
        )

        quantity = PortfolioAnalytics.ZERO
        invested_value = PortfolioAnalytics.ZERO

        for tx in transactions:
            tx_quantity = (
                tx.quantity
                or PortfolioAnalytics.ZERO
            )

            amount = (
                tx.amount
                or PortfolioAnalytics.ZERO
            )

            fees = (
                tx.fees
                or PortfolioAnalytics.ZERO
            )

            if tx.transaction_type in (
                TransactionType.BUY,
                TransactionType.SIP,
            ):
                quantity += tx_quantity
                invested_value += amount + fees

            elif tx.transaction_type == TransactionType.SELL:
                if quantity <= 0:
                    continue

                average_cost = (
                    invested_value / quantity
                    if quantity
                    else PortfolioAnalytics.ZERO
                )

                quantity -= tx_quantity

                invested_value -= (
                    average_cost * tx_quantity
                )

                if quantity <= 0:
                    quantity = PortfolioAnalytics.ZERO
                    invested_value = PortfolioAnalytics.ZERO

        return {
            "quantity": quantity,
            "invested_value": invested_value,
        }

    @staticmethod
    def calculate_historical_value(
        user,
        target_date,
    ):
        assets = (
            Asset.objects
            .filter(
                PortfolioAnalytics._scope_q(user),
                is_active=True,
            )
        )

        total_value = PortfolioAnalytics.ZERO
        total_invested = PortfolioAnalytics.ZERO

        for asset in assets:
            position = (
                PortfolioAnalytics
                .calculate_position_as_of(
                    asset,
                    target_date,
                )
            )

            quantity = position["quantity"]
            invested_value = position["invested_value"]

            if quantity <= 0:
                continue

            price_record = (
                MarketPrice.objects
                .filter(
                    asset=asset,
                    date__lte=target_date,
                )
                .order_by("-date")
                .first()
            )

            if not price_record:
                continue

            current_price = price_record.close_price

            current_value = (
                quantity * current_price
            )

            total_value += current_value
            total_invested += invested_value

        return {
            "date": target_date,
            "invested_value": total_invested,
            "portfolio_value": total_value,
            "pnl": (
                total_value
                - total_invested
            ),
        }