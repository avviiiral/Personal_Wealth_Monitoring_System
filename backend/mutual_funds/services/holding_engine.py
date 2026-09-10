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
    """Calculate mutual-fund holdings inside a family boundary."""

    ZERO = Decimal("0")

    @staticmethod
    def get_transactions(scheme, family_group_id=None):
        """Return transactions for a scheme, strictly within its family."""
        if family_group_id is None:
            family_group_id = scheme.family_group_id
        if family_group_id is None:
            raise ValueError("family_group_id is required for mutual-fund holding operations.")

        return (
            MutualFundTransaction.objects
            .filter(
                scheme=scheme,
                family_group_id=family_group_id,
            )
            .order_by("transaction_date", "created_at", "id")
        )

    @staticmethod
    def calculate_position(scheme, family_group_id=None):
        units = MutualFundHoldingEngine.ZERO
        invested_value = MutualFundHoldingEngine.ZERO

        transactions = MutualFundHoldingEngine.get_transactions(
            scheme,
            family_group_id=family_group_id,
        )

        for tx in transactions:
            tx_units = tx.units or MutualFundHoldingEngine.ZERO
            amount = tx.amount or MutualFundHoldingEngine.ZERO

            if tx.transaction_type in (
                MutualFundTransactionType.PURCHASE,
                MutualFundTransactionType.SIP,
            ):
                units += tx_units
                invested_value += amount

            elif tx.transaction_type == MutualFundTransactionType.REDEMPTION:
                if units <= 0 or tx_units <= 0:
                    continue

                average_cost = invested_value / units if units else MutualFundHoldingEngine.ZERO
                units -= tx_units
                invested_value -= average_cost * tx_units

                if units <= 0:
                    units = MutualFundHoldingEngine.ZERO
                    invested_value = MutualFundHoldingEngine.ZERO

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
        return (
            MutualFundNAV.objects
            .filter(scheme=scheme)
            .order_by("-date")
            .first()
        )

    @staticmethod
    @transaction.atomic
    def rebuild_holding(scheme, family_group_id=None):
        if family_group_id is None:
            family_group_id = scheme.family_group_id
        if family_group_id is None:
            raise ValueError("family_group_id is required for mutual-fund holding operations.")
        if scheme.family_group_id != family_group_id:
            raise ValueError("Mutual fund scheme does not belong to the requested family.")

        position = MutualFundHoldingEngine.calculate_position(
            scheme,
            family_group_id=family_group_id,
        )
        units = position["units"]
        invested_value = position["invested_value"]
        average_nav = position["average_nav"]

        latest_nav = MutualFundHoldingEngine.get_latest_nav(scheme)
        current_nav = latest_nav.nav if latest_nav else MutualFundHoldingEngine.ZERO
        current_value = units * current_nav
        unrealized_pnl = current_value - invested_value

        holding, _ = MutualFundHolding.objects.update_or_create(
            scheme=scheme,
            family_group_id=family_group_id,
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
        return holding

    @staticmethod
    def rebuild_all_for_family(family_group_id):
        if family_group_id is None:
            raise ValueError("family_group_id is required for mutual-fund holding operations.")

        schemes = MutualFundScheme.objects.filter(
            family_group_id=family_group_id,
            is_active=True,
        )

        return [
            MutualFundHoldingEngine.rebuild_holding(
                scheme,
                family_group_id=family_group_id,
            )
            for scheme in schemes
        ]

    @staticmethod
    def rebuild_all_for_user(user):
        """Compatibility wrapper for legacy callers during migration."""
        schemes = MutualFundScheme.objects.filter(owner=user, is_active=True)
        return [
            MutualFundHoldingEngine.rebuild_holding(
                scheme,
                family_group_id=scheme.family_group_id,
            )
            for scheme in schemes
            if scheme.family_group_id is not None
        ]
