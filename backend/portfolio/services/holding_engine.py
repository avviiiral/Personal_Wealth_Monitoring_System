from decimal import Decimal

from django.db import transaction
from django.db.models import Sum

from investments.models import (
    Asset,
    Holding,
    Transaction,
    TransactionType,
)

from market_data.models import (
    DataSource,
    MarketPrice,
)


class HoldingCalculationEngine:
    """
    Calculates the current holding for an asset.

    Price priority:

        1. Manual MarketPrice
        2. Automatic MarketPrice
        3. Zero

    Manual price is given priority because the user
    explicitly entered it for an asset whose automatic
    market data may be unavailable or incorrect.
    """

    ZERO = Decimal("0")

    # ==========================================================
    # TRANSACTIONS
    # ==========================================================

    @staticmethod
    def get_transactions(asset):

        return (
            Transaction.objects
            .filter(
                asset=asset,
                family=asset.family,
            )
            .order_by(
                "transaction_date",
                "created_at",
                "id",
            )
        )

    # ==========================================================
    # POSITION
    # ==========================================================

    @staticmethod
    def calculate_position(asset):

        quantity = (
            HoldingCalculationEngine.ZERO
        )

        invested_value = (
            HoldingCalculationEngine.ZERO
        )

        transactions = (
            HoldingCalculationEngine
            .get_transactions(asset)
        )

        # The common transaction types used for position math can be
        # reduced directly in SQL. This removes the Python row-by-row
        # loop for the normal BUY/SIP-only path while preserving the
        # existing sell/bonus/split logic when those transaction types
        # are present.
        has_adjustments = transactions.exclude(
            transaction_type__in=(
                TransactionType.BUY,
                TransactionType.SIP,
            )
        ).exists()

        if not has_adjustments:
            totals = transactions.aggregate(
                quantity=Sum("quantity"),
                invested_value=Sum("amount"),
            )
            quantity = totals["quantity"] or HoldingCalculationEngine.ZERO
            invested_value = totals["invested_value"] or HoldingCalculationEngine.ZERO
            average_cost = (
                invested_value / quantity
                if quantity > 0
                else HoldingCalculationEngine.ZERO
            )
            return {
                "quantity": quantity,
                "invested_value": invested_value,
                "average_cost": average_cost,
            }

        for tx in transactions:

            tx_type = (
                tx.transaction_type
            )

            tx_quantity = (
                tx.quantity
                or HoldingCalculationEngine.ZERO
            )

            tx_amount = (
                tx.amount
                or HoldingCalculationEngine.ZERO
            )

            # --------------------------------------------------
            # BUY / SIP
            # --------------------------------------------------

            if tx_type in (
                TransactionType.BUY,
                TransactionType.SIP,
            ):

                if tx_quantity > 0:
                    quantity += tx_quantity

                if tx_amount > 0:
                    invested_value += tx_amount

            # --------------------------------------------------
            # SELL
            # --------------------------------------------------

            elif tx_type == TransactionType.SELL:

                if tx_quantity <= 0:
                    continue

                if quantity <= 0:
                    continue

                average_cost = (
                    invested_value / quantity
                    if quantity > 0
                    else HoldingCalculationEngine.ZERO
                )

                sell_quantity = min(
                    tx_quantity,
                    quantity,
                )

                quantity -= sell_quantity

                invested_value -= (
                    average_cost
                    * sell_quantity
                )

                if quantity <= 0:

                    quantity = (
                        HoldingCalculationEngine.ZERO
                    )

                    invested_value = (
                        HoldingCalculationEngine.ZERO
                    )

            # --------------------------------------------------
            # BONUS
            # --------------------------------------------------

            elif tx_type == TransactionType.BONUS:

                if tx_quantity > 0:
                    quantity += tx_quantity

            # --------------------------------------------------
            # SPLIT
            # --------------------------------------------------

            elif tx_type == TransactionType.SPLIT:

                if tx_quantity > 0:
                    quantity += tx_quantity

            # --------------------------------------------------
            # OTHER
            # --------------------------------------------------

            elif tx_type in (
                TransactionType.DIVIDEND,
                TransactionType.INTEREST,
                TransactionType.DEPOSIT,
                TransactionType.WITHDRAWAL,
                TransactionType.OTHER,
            ):
                continue

        average_cost = (
            invested_value / quantity
            if quantity > 0
            else HoldingCalculationEngine.ZERO
        )

        return {
            "quantity": quantity,
            "invested_value": invested_value,
            "average_cost": average_cost,
        }

    # ==========================================================
    # LATEST AUTOMATIC PRICE
    # ==========================================================

    @staticmethod
    def get_latest_price(asset):

        return (
            MarketPrice.objects
            .filter(
                asset=asset,
            )
            .exclude(
                source=DataSource.MANUAL,
            )
            .order_by(
                "-date",
                "-id",
            )
            .first()
        )

    # ==========================================================
    # EFFECTIVE PRICE
    # ==========================================================

    @staticmethod
    def get_effective_price(asset):
        """
        Determine the same effective current price used by the Portfolio Tree.

        Priority and fallbacks are centralized in PortfolioTreeService so the
        Dashboard Holding rows cannot diverge from Portfolio valuation for
        mutual-fund NAVs, REIT/InvIT reference prices, manual overrides, or
        automatic market prices.
        """

        # PortfolioTreeService uses transaction metadata (asset class/subclass)
        # to identify legacy mutual-fund and REIT/InvIT rows. Populate that
        # metadata on the Asset before asking it for the shared price cache.
        latest_transaction = (
            Transaction.objects
            .filter(asset=asset)
            .order_by("-transaction_date", "-created_at", "-id")
            .first()
        )
        if latest_transaction is not None:
            asset._portfolio_asset_class = latest_transaction.asset_class
            asset._portfolio_sub_class = latest_transaction.sub_class

        from portfolio.services.portfolio_tree_service import PortfolioTreeService

        price_data = PortfolioTreeService._load_price_cache(
            {asset.id},
            assets_by_id={asset.id: asset},
        ).get(asset.id)

        if price_data is None:
            return {
                "price": HoldingCalculationEngine.ZERO,
                "source": None,
                "date": None,
                "is_manual": False,
                "has_price": False,
            }

        return {
            "price": (
                price_data.get("current_price")
                if price_data.get("current_price") is not None
                else HoldingCalculationEngine.ZERO
            ),
            "source": price_data.get("price_source"),
            "date": price_data.get("price_date"),
            "is_manual": price_data.get("price_source") == DataSource.MANUAL,
            "has_price": price_data.get("current_price") is not None,
        }

    # ==========================================================
    # REBUILD HOLDING
    # ==========================================================

    @staticmethod
    @transaction.atomic
    def rebuild_holding(asset):

        position = (
            HoldingCalculationEngine
            .calculate_position(asset)
        )

        quantity = (
            position["quantity"]
        )

        invested_value = (
            position["invested_value"]
        )

        average_cost = (
            position["average_cost"]
        )

        effective_price = (
            HoldingCalculationEngine
            .get_effective_price(asset)
        )

        current_price = (
            effective_price["price"]
        )

        current_value = (
            quantity
            * current_price
        )

        unrealized_pnl = (
            current_value - invested_value
            if effective_price["has_price"]
            else HoldingCalculationEngine.ZERO
        )

        holding, _ = (
            Holding.objects
            .update_or_create(
                asset=asset,
                defaults={
                    "owner": asset.owner,
                    "family": asset.family,
                    "quantity": quantity,
                    "average_cost": average_cost,
                    "invested_value": invested_value,
                    "current_price": current_price,
                    "current_value": current_value,
                    "unrealized_pnl": unrealized_pnl,
                },
            )
        )

        return holding

    # ==========================================================
    # REBUILD ALL
    # ==========================================================

    @staticmethod
    def rebuild_all_for_user(user):
        from users.permissions import require_active_family

        family = require_active_family(user)

        assets = (
            Asset.objects
            .filter(
                family=family,
                is_active=True,
            )
        )

        holdings = []

        for asset in assets:
            holding = (
                HoldingCalculationEngine
                .rebuild_holding(
                    asset
                )
            )
            holdings.append(holding)

        return holdings