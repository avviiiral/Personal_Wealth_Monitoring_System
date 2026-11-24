from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP
from types import SimpleNamespace

from django.db import transaction
from django.utils import timezone

from mf_intelligence.models import MFScheme
from mutual_funds.models import (
    AMFIMasterNAV,
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
                family=scheme.family,
            )
            .only(
                "transaction_type",
                "units",
                "amount",
                "transaction_date",
                "created_at",
                "id",
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
        """Return the freshest family NAV, using recent MF Intelligence as fallback."""
        today = timezone.localdate()
        family_nav = (
            MutualFundNAV.objects
            .filter(scheme=scheme, date__lte=today, nav__gt=0)
            .only("nav", "date")
            .order_by("-date", "-id")
            .first()
        )
        master_nav = None
        intelligence_nav = None
        if scheme.scheme_code:
            master_nav = (
                AMFIMasterNAV.objects.filter(
                    scheme__scheme_code=scheme.scheme_code,
                    source="AMFI",
                    date__lte=today,
                    nav__gt=0,
                )
                .only("nav", "date")
                .order_by("-date", "-id")
                .first()
            )
            intelligence_nav = (
                MFScheme.objects.filter(
                    amfi_code=scheme.scheme_code,
                    latest_nav__gt=0,
                    latest_nav_date__lte=today,
                    latest_nav_date__gte=today - timedelta(days=7),
                )
                .only("latest_nav", "latest_nav_date")
                .first()
            )

        candidates = []
        if family_nav is not None:
            candidates.append((family_nav.date, 0, "family", family_nav))
        if master_nav is not None:
            candidates.append((master_nav.date, 2, "amfi", master_nav))
        if intelligence_nav is not None:
            candidates.append((
                intelligence_nav.latest_nav_date,
                1,
                "intelligence",
                intelligence_nav,
            ))
        if not candidates:
            return None

        _as_of, _priority, source, selected = max(
            candidates,
            key=lambda item: (item[0], item[1]),
        )
        if source == "intelligence":
            return SimpleNamespace(
                nav=selected.latest_nav,
                date=selected.latest_nav_date,
                source="MF_INTELLIGENCE",
            )
        return selected

    @staticmethod
    def get_valuation(holding):
        """Calculate response-time holding values from the freshest eligible NAV.

        This is read-through only: it does not write family-owned holding or NAV
        records. When no usable NAV is available, retain the stored valuation.
        """
        nav_record = MutualFundHoldingEngine.get_latest_nav(holding.scheme)
        current_nav = (
            nav_record.nav
            if nav_record is not None and nav_record.nav > 0
            else holding.current_nav
        )
        if current_nav is not None and current_nav > 0:
            current_value = (
                (holding.units or MutualFundHoldingEngine.ZERO) * current_nav
            ).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
        else:
            current_value = holding.current_value or MutualFundHoldingEngine.ZERO
        unrealized_pnl = (
            current_value - (holding.invested_value or MutualFundHoldingEngine.ZERO)
        ).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
        return {
            "current_nav": current_nav or MutualFundHoldingEngine.ZERO,
            "current_value": current_value,
            "unrealized_pnl": unrealized_pnl,
        }

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
                    "family": scheme.family,
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

        from users.permissions import require_active_family

        family = require_active_family(user)

        schemes = (
            MutualFundScheme.objects
            .filter(
                family=family,
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