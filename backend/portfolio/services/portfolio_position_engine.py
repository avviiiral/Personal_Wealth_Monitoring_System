from decimal import Decimal

from django.db import transaction

from investments.models import (
    Asset,
    PortfolioPosition,
    Transaction,
    TransactionType,
)
from market_data.models import MarketPrice
from users.permissions import require_active_family


class PortfolioPositionEngine:

    ZERO = Decimal("0")

    @staticmethod
    def get_transactions(
        family,
        family_name,
        portfolio,
        asset,
    ):
        return (
            Transaction.objects
            .filter(
                family=family,
                family_name=family_name,
                portfolio=portfolio,
                asset=asset,
            )
            .order_by(
                "transaction_date",
                "created_at",
                "id",
            )
        )

    @classmethod
    def calculate_position(
        cls,
        family,
        family_name,
        portfolio,
        asset,
    ):
        quantity = cls.ZERO
        invested_value = cls.ZERO

        transactions = cls.get_transactions(
            family=family,
            family_name=family_name,
            portfolio=portfolio,
            asset=asset,
        )

        for tx in transactions:
            tx_quantity = tx.quantity or cls.ZERO
            tx_amount = tx.amount or cls.ZERO

            if tx.transaction_type in (
                TransactionType.BUY,
                TransactionType.SIP,
            ):
                if tx_quantity > 0:
                    quantity += tx_quantity
                if tx_amount > 0:
                    invested_value += tx_amount

            elif tx.transaction_type == TransactionType.SELL:
                if tx_quantity <= 0 or quantity <= 0:
                    continue

                average_cost = (
                    invested_value / quantity
                    if quantity > 0
                    else cls.ZERO
                )
                sell_quantity = min(tx_quantity, quantity)
                quantity -= sell_quantity
                invested_value -= average_cost * sell_quantity

                if quantity <= 0:
                    quantity = cls.ZERO
                    invested_value = cls.ZERO

            elif tx.transaction_type == TransactionType.BONUS:
                if tx_quantity > 0:
                    quantity += tx_quantity

            elif tx.transaction_type == TransactionType.SPLIT:
                if tx_quantity > 0:
                    quantity += tx_quantity

        average_cost = (
            invested_value / quantity
            if quantity > 0
            else cls.ZERO
        )

        return {
            "quantity": quantity,
            "invested_value": invested_value,
            "average_cost": average_cost,
        }

    @staticmethod
    def get_latest_price(asset):
        return (
            MarketPrice.objects
            .filter(asset=asset)
            .order_by("-date")
            .first()
        )

    @classmethod
    @transaction.atomic
    def rebuild_position(
        cls,
        family_name,
        portfolio,
        asset,
        family=None,
    ):
        family = family or asset.family

        if family is None:
            raise ValueError(
                "Portfolio positions require a family."
            )

        position = cls.calculate_position(
            family=family,
            family_name=family_name,
            portfolio=portfolio,
            asset=asset,
        )

        quantity = position["quantity"]
        invested_value = position["invested_value"]
        average_cost = position["average_cost"]

        latest_price = cls.get_latest_price(asset)
        current_price = (
            latest_price.close_price
            if latest_price
            else cls.ZERO
        )
        current_value = quantity * current_price
        gain = current_value - invested_value

        portfolio_position, _ = (
            PortfolioPosition.objects
            .update_or_create(
                family=family,
                family_name=family_name,
                portfolio=portfolio,
                asset=asset,
                defaults={
                    "owner": None,
                    "quantity": quantity,
                    "average_cost": average_cost,
                    "invested_value": invested_value,
                    "current_price": current_price,
                    "current_value": current_value,
                    "gain": gain,
                },
            )
        )

        return portfolio_position

    @classmethod
    def rebuild_all_for_user(cls, user):
        family = require_active_family(user)

        combinations = (
            Transaction.objects
            .filter(family=family)
            .values(
                "family_name",
                "portfolio",
                "asset_id",
            )
            .distinct()
        )

        positions = []

        for combination in combinations:
            asset = Asset.objects.get(id=combination["asset_id"])

            position = cls.rebuild_position(
                family=family,
                family_name=combination["family_name"],
                portfolio=combination["portfolio"],
                asset=asset,
            )
            positions.append(position)

        return positions