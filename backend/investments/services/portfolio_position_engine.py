from decimal import Decimal

from django.db import transaction

from investments.models import (
    Asset,
    PortfolioPosition,
    Transaction,
    TransactionType,
)

from market_data.models import MarketPrice


class PortfolioPositionEngine:
    """
    Calculates current positions grouped by:

        Family
        Portfolio
        Asset

    Transaction data is the source of truth.

    Current price is taken only from the latest
    available market price.
    """

    ZERO = Decimal("0")

    @staticmethod
    def get_latest_price(asset):
        """
        Return the latest available market price.
        """

        latest_price = (
            MarketPrice.objects
            .filter(asset=asset)
            .order_by("-date")
            .first()
        )

        if latest_price is None:
            return PortfolioPositionEngine.ZERO

        return (
            latest_price.close_price
            or PortfolioPositionEngine.ZERO
        )

    @staticmethod
    def calculate_position(
        owner,
        family_name,
        portfolio,
        asset,
    ):
        """
        Calculate one Family + Portfolio + Asset position.
        """

        transactions = (
            Transaction.objects
            .filter(
                owner=owner,
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

        quantity = PortfolioPositionEngine.ZERO
        invested_value = PortfolioPositionEngine.ZERO

        for tx in transactions:

            tx_quantity = (
                tx.quantity
                or PortfolioPositionEngine.ZERO
            )

            tx_amount = (
                tx.amount
                or PortfolioPositionEngine.ZERO
            )

            if tx.transaction_type in (
                TransactionType.BUY,
                TransactionType.SIP,
            ):
                quantity += tx_quantity
                invested_value += tx_amount

            elif tx.transaction_type == TransactionType.SELL:

                if tx_quantity <= 0:
                    continue

                if quantity <= 0:
                    continue

                average_cost = (
                    invested_value / quantity
                    if quantity > 0
                    else PortfolioPositionEngine.ZERO
                )

                quantity -= tx_quantity

                invested_value -= (
                    average_cost * tx_quantity
                )

                if quantity <= 0:
                    quantity = (
                        PortfolioPositionEngine.ZERO
                    )

                    invested_value = (
                        PortfolioPositionEngine.ZERO
                    )

        average_cost = (
            invested_value / quantity
            if quantity > 0
            else PortfolioPositionEngine.ZERO
        )

        current_price = (
            PortfolioPositionEngine
            .get_latest_price(asset)
        )

        current_value = (
            quantity * current_price
        )

        gain = (
            current_value - invested_value
        )

        return {
            "quantity": quantity,
            "average_cost": average_cost,
            "invested_value": invested_value,
            "current_price": current_price,
            "current_value": current_value,
            "gain": gain,
        }

    @staticmethod
    @transaction.atomic
    def rebuild_position(
        owner,
        family_name,
        portfolio,
        asset,
    ):
        """
        Rebuild one Family + Portfolio + Asset position.
        """

        calculated = (
            PortfolioPositionEngine
            .calculate_position(
                owner=owner,
                family_name=family_name,
                portfolio=portfolio,
                asset=asset,
            )
        )

        position, _ = (
            PortfolioPosition.objects
            .update_or_create(
                owner=owner,
                family_name=family_name,
                portfolio=portfolio,
                asset=asset,
                defaults=calculated,
            )
        )

        return position

    @staticmethod
    def rebuild_all_for_user(owner):
        """
        Rebuild all portfolio positions for a user.
        """

        groups = (
            Transaction.objects
            .filter(owner=owner)
            .values(
                "family_name",
                "portfolio",
                "asset_id",
            )
            .distinct()
        )

        positions = []

        for group in groups:

            family_name = group["family_name"]
            portfolio = group["portfolio"]
            asset_id = group["asset_id"]

            if not family_name or not portfolio:
                continue

            asset = Asset.objects.get(
                id=asset_id,
            )

            position = (
                PortfolioPositionEngine
                .rebuild_position(
                    owner=owner,
                    family_name=family_name,
                    portfolio=portfolio,
                    asset=asset,
                )
            )

            positions.append(position)

        return positions