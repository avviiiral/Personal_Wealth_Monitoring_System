from decimal import Decimal

from django.db import transaction

from mutual_funds.models import (
    MutualFundHolding,
    MutualFundNAV,
    MutualFundScheme,
    MutualFundTransaction,
    MutualFundTransactionType,
)


class MutualFundHoldingEngine:
    """
    Calculates mutual-fund holdings from transactions
    and the latest available NAV.
    """

    ZERO = Decimal("0")

    @staticmethod
    def get_transactions(scheme):
        """
        Return all transactions for a scheme in chronological order.
        """

        return (
            MutualFundTransaction.objects
            .filter(
                scheme=scheme,
                owner=scheme.owner,
            )
            .order_by(
                "transaction_date",
                "created_at",
                "id",
            )
        )

    @staticmethod
    def calculate_position(scheme):
        """
        Calculate current units and invested value.

        PURCHASE / SIP:
            Increase units and invested value.

        REDEMPTION:
            Reduce units and reduce invested value
            using average-cost methodology.

        DIVIDEND:
            Does not change units or invested value.
        """

        units = MutualFundHoldingEngine.ZERO
        invested_value = MutualFundHoldingEngine.ZERO

        transactions = (
            MutualFundHoldingEngine
            .get_transactions(scheme)
        )

        for tx in transactions:

            tx_units = (
                tx.units
                or MutualFundHoldingEngine.ZERO
            )

            amount = (
                tx.amount
                or MutualFundHoldingEngine.ZERO
            )

            if tx.transaction_type in (
                MutualFundTransactionType.PURCHASE,
                MutualFundTransactionType.SIP,
            ):

                units += tx_units
                invested_value += amount

            elif (
                tx.transaction_type
                == MutualFundTransactionType.REDEMPTION
            ):

                if units <= 0:
                    continue

                if tx_units <= 0:
                    continue

                average_cost = (
                    invested_value / units
                    if units
                    else MutualFundHoldingEngine.ZERO
                )

                units -= tx_units

                invested_value -= (
                    average_cost * tx_units
                )

                if units <= 0:

                    units = (
                        MutualFundHoldingEngine.ZERO
                    )

                    invested_value = (
                        MutualFundHoldingEngine.ZERO
                    )

        average_nav = (
            invested_value / units
            if units > 0
            else MutualFundHoldingEngine.ZERO
        )

        return {
            "units": units,
            "invested_value": invested_value,
            "average_nav": average_nav,
        }

    @staticmethod
    def get_latest_nav(scheme):
        """
        Get the latest NAV available for the scheme.
        """

        return (
            MutualFundNAV.objects
            .filter(
                scheme=scheme,
            )
            .order_by("-date")
            .first()
        )

    @staticmethod
    @transaction.atomic
    def rebuild_holding(scheme):
        """
        Recalculate and save the current holding.
        """

        position = (
            MutualFundHoldingEngine
            .calculate_position(scheme)
        )

        units = position["units"]

        invested_value = (
            position["invested_value"]
        )

        average_nav = (
            position["average_nav"]
        )

        latest_nav = (
            MutualFundHoldingEngine
            .get_latest_nav(scheme)
        )

        if latest_nav:

            current_nav = (
                latest_nav.nav
            )

        else:

            current_nav = (
                MutualFundHoldingEngine.ZERO
            )

        current_value = (
            units * current_nav
        )

        unrealized_pnl = (
            current_value
            - invested_value
        )

        holding, _ = (
            MutualFundHolding.objects
            .update_or_create(
                scheme=scheme,
                defaults={
                    "owner": scheme.owner,
                    "units": units,
                    "invested_value": invested_value,
                    "average_nav": average_nav,
                    "current_nav": current_nav,
                    "current_value": current_value,
                    "unrealized_pnl": unrealized_pnl,
                },
            )
        )

        return holding

    @staticmethod
    def rebuild_all_for_user(user):
        """
        Rebuild holdings for all active mutual-fund
        schemes belonging to a user.
        """

        schemes = (
            MutualFundScheme.objects
            .filter(
                owner=user,
                is_active=True,
            )
        )

        holdings = []

        for scheme in schemes:

            holding = (
                MutualFundHoldingEngine
                .rebuild_holding(scheme)
            )

            holdings.append(holding)

        return holdings