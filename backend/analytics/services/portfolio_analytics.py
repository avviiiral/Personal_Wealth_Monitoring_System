from datetime import date
from decimal import Decimal

from django.db.models import Count, Sum, Q

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
    def _calculate_xirr_from_transactions(transactions, current_value):
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

        cash_flows.sort(key=lambda item: item[0])

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

        holdings = PortfolioAnalytics.get_holdings(user)

        current_value = sum(
            (
                holding.current_value
                or PortfolioAnalytics.ZERO
            )
            for holding in holdings
        )

        return PortfolioAnalytics._calculate_xirr_from_transactions(
            transactions,
            current_value,
        )

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
    def _calculate_realized_pnl_from_transactions(transactions):
        positions = {}
        realized_pnl = PortfolioAnalytics.ZERO

        for tx in transactions:
            asset_id = tx.asset_id

            if asset_id not in positions:
                positions[asset_id] = {
                    "quantity": PortfolioAnalytics.ZERO,
                    "invested_value": PortfolioAnalytics.ZERO,
                }

            position = positions[asset_id]

            quantity = tx.quantity or PortfolioAnalytics.ZERO
            amount = tx.amount or PortfolioAnalytics.ZERO
            fees = tx.fees or PortfolioAnalytics.ZERO

            if tx.transaction_type in (
                TransactionType.BUY,
                TransactionType.SIP,
            ):
                position["quantity"] += quantity
                position["invested_value"] += amount + fees

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

                cost_of_sale = average_cost * quantity

                realized_pnl += (
                    amount
                    - fees
                    - cost_of_sale
                )

                position["quantity"] -= quantity
                position["invested_value"] -= cost_of_sale

                if position["quantity"] <= 0:
                    position["quantity"] = PortfolioAnalytics.ZERO
                    position["invested_value"] = PortfolioAnalytics.ZERO

        return realized_pnl

    @staticmethod
    def calculate_realized_pnl(user):
        transactions = (
            Transaction.objects
            .filter(PortfolioAnalytics._scope_q(user))
            .order_by(
                "asset_id",
                "transaction_date",
                "created_at",
                "id",
            )
        )

        return PortfolioAnalytics._calculate_realized_pnl_from_transactions(
            transactions
        )

    @staticmethod
    def calculate_summary(user):
        holdings = PortfolioAnalytics.get_holdings(user)

        totals = holdings.aggregate(
            invested=Sum("invested_value"),
            current=Sum("current_value"),
            unrealized=Sum("unrealized_pnl"),
            number_of_holdings=Count("id"),
        )

        total_invested = totals["invested"] or PortfolioAnalytics.ZERO
        total_current_value = totals["current"] or PortfolioAnalytics.ZERO
        unrealized_pnl = totals["unrealized"] or PortfolioAnalytics.ZERO

        transactions = list(
            Transaction.objects
            .filter(PortfolioAnalytics._scope_q(user))
            .order_by(
                "asset_id",
                "transaction_date",
                "created_at",
                "id",
            )
        )

        realized_pnl = (
            PortfolioAnalytics
            ._calculate_realized_pnl_from_transactions(transactions)
        )

        total_pnl = realized_pnl + unrealized_pnl

        return_percentage = (
            (total_pnl / total_invested) * 100
            if total_invested
            else PortfolioAnalytics.ZERO
        )

        xirr = PortfolioAnalytics._calculate_xirr_from_transactions(
            transactions,
            total_current_value,
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
            "xirr_percentage": xirr,
            "number_of_holdings": totals["number_of_holdings"] or 0,
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
        # Calculate the ranking fields in SQL and fetch only the columns
        # required by the response. This avoids materializing full Holding
        # and Asset model instances for large portfolios.
        from django.db.models import Case, F, When, DecimalField, ExpressionWrapper

        pnl_percentage = Case(
            When(
                invested_value__gt=0,
                then=ExpressionWrapper(
                    F("unrealized_pnl") * 100 / F("invested_value"),
                    output_field=DecimalField(max_digits=24, decimal_places=8),
                ),
            ),
            default=PortfolioAnalytics.ZERO,
            output_field=DecimalField(max_digits=24, decimal_places=8),
        )

        rows = (
            PortfolioAnalytics
            .get_holdings(user)
            .annotate(pnl_percentage=pnl_percentage)
            .values(
                "asset_id",
                "asset__name",
                "asset__symbol",
                "current_value",
                "unrealized_pnl",
                "pnl_percentage",
            )
            .order_by("-pnl_percentage")
        )

        results = []
        for row in rows:
            results.append(
                {
                    "asset_id": row["asset_id"],
                    "asset_name": row["asset__name"],
                    "symbol": row["asset__symbol"],
                    "current_value": row["current_value"],
                    "unrealized_pnl": row["unrealized_pnl"],
                    "pnl_percentage": round(
                        row["pnl_percentage"],
                        2,
                    ),
                }
            )

        return results

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
    def calculate_historical_values(
        user,
        start_date,
        end_date,
    ):
        """
        Calculate historical portfolio values for an inclusive date range
        using one bulk transaction load and one bulk price load.

        This preserves calculate_historical_value()'s equity-only
        calculation while avoiding a database round trip per day.
        """

        if start_date > end_date:
            return []

        assets = list(
            Asset.objects
            .filter(
                PortfolioAnalytics._scope_q(user),
                is_active=True,
            )
            .only("id")
        )

        dates = []
        current_date = start_date
        while current_date <= end_date:
            dates.append(current_date)
            current_date += __import__("datetime").timedelta(days=1)

        if not assets:
            return [
                {
                    "date": target_date,
                    "invested_value": PortfolioAnalytics.ZERO,
                    "portfolio_value": PortfolioAnalytics.ZERO,
                    "pnl": PortfolioAnalytics.ZERO,
                }
                for target_date in dates
            ]

        asset_ids = [asset.id for asset in assets]

        transactions = list(
            Transaction.objects
            .filter(
                asset_id__in=asset_ids,
                transaction_date__lte=end_date,
            )
            .order_by(
                "asset_id",
                "transaction_date",
                "created_at",
                "id",
            )
        )

        transactions_by_asset = {}
        for tx in transactions:
            transactions_by_asset.setdefault(tx.asset_id, []).append(tx)

        prices_by_asset = {}
        prices = (
            MarketPrice.objects
            .filter(
                asset_id__in=asset_ids,
                date__lte=end_date,
            )
            .order_by(
                "asset_id",
                "date",
                "id",
            )
            .only("asset_id", "date", "close_price")
        )

        for price in prices:
            prices_by_asset.setdefault(price.asset_id, []).append(
                (price.date, price.close_price)
            )

        positions = {
            asset.id: {
                "quantity": PortfolioAnalytics.ZERO,
                "invested_value": PortfolioAnalytics.ZERO,
            }
            for asset in assets
        }
        transaction_indexes = {
            asset.id: 0
            for asset in assets
        }
        price_indexes = {
            asset.id: -1
            for asset in assets
        }

        results = []

        for target_date in dates:
            total_value = PortfolioAnalytics.ZERO
            total_invested = PortfolioAnalytics.ZERO

            for asset in assets:
                asset_id = asset.id
                asset_transactions = transactions_by_asset.get(
                    asset_id,
                    [],
                )
                position = positions[asset_id]
                tx_index = transaction_indexes[asset_id]

                while (
                    tx_index < len(asset_transactions)
                    and asset_transactions[tx_index].transaction_date
                    <= target_date
                ):
                    tx = asset_transactions[tx_index]
                    quantity = tx.quantity or PortfolioAnalytics.ZERO
                    amount = tx.amount or PortfolioAnalytics.ZERO
                    fees = tx.fees or PortfolioAnalytics.ZERO

                    if tx.transaction_type in (
                        TransactionType.BUY,
                        TransactionType.SIP,
                    ):
                        position["quantity"] += quantity
                        position["invested_value"] += amount + fees

                    elif tx.transaction_type == TransactionType.SELL:
                        if position["quantity"] > 0:
                            average_cost = (
                                position["invested_value"]
                                / position["quantity"]
                            )
                            position["quantity"] -= quantity
                            position["invested_value"] -= (
                                average_cost * quantity
                            )

                            if position["quantity"] <= 0:
                                position["quantity"] = PortfolioAnalytics.ZERO
                                position["invested_value"] = PortfolioAnalytics.ZERO

                    tx_index += 1

                transaction_indexes[asset_id] = tx_index

                if position["quantity"] <= 0:
                    continue

                asset_prices = prices_by_asset.get(asset_id, [])
                price_index = price_indexes[asset_id]

                while (
                    price_index + 1 < len(asset_prices)
                    and asset_prices[price_index + 1][0] <= target_date
                ):
                    price_index += 1

                price_indexes[asset_id] = price_index

                if price_index < 0:
                    continue

                current_value = (
                    position["quantity"]
                    * asset_prices[price_index][1]
                )
                total_value += current_value
                total_invested += position["invested_value"]

            results.append(
                {
                    "date": target_date,
                    "invested_value": total_invested,
                    "portfolio_value": total_value,
                    "pnl": total_value - total_invested,
                }
            )

        return results

    @staticmethod
    def calculate_historical_value(
        user,
        target_date,
    ):
        assets = list(
            Asset.objects
            .filter(
                PortfolioAnalytics._scope_q(user),
                is_active=True,
            )
            .only("id")
        )

        if not assets:
            return {
                "date": target_date,
                "invested_value": PortfolioAnalytics.ZERO,
                "portfolio_value": PortfolioAnalytics.ZERO,
                "pnl": PortfolioAnalytics.ZERO,
            }

        asset_ids = [asset.id for asset in assets]

        transactions_by_asset = {
            asset_id: []
            for asset_id in asset_ids
        }

        transactions = (
            Transaction.objects
            .filter(
                asset_id__in=asset_ids,
                transaction_date__lte=target_date,
            )
            .order_by(
                "asset_id",
                "transaction_date",
                "created_at",
                "id",
            )
        )

        for tx in transactions:
            transactions_by_asset[tx.asset_id].append(tx)

        latest_prices_by_asset = {}
        prices = (
            MarketPrice.objects
            .filter(
                asset_id__in=asset_ids,
                date__lte=target_date,
            )
            .order_by("asset_id", "-date")
            .only(
                "asset_id",
                "date",
                "close_price",
            )
        )

        for price in prices:
            latest_prices_by_asset.setdefault(
                price.asset_id,
                price,
            )

        total_value = PortfolioAnalytics.ZERO
        total_invested = PortfolioAnalytics.ZERO

        for asset in assets:
            quantity = PortfolioAnalytics.ZERO
            invested_value = PortfolioAnalytics.ZERO

            for tx in transactions_by_asset[asset.id]:
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

            if quantity <= 0:
                continue

            price_record = latest_prices_by_asset.get(asset.id)

            if not price_record:
                continue

            current_value = (
                quantity * price_record.close_price
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