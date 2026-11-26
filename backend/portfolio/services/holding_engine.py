from decimal import Decimal

from django.db import transaction
from django.db.models import Sum

from corporate_actions.position_adjustment import (
    order_ledger,
    replay_ledger,
)

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

        # Ledger order: date, then corporate-action quantity rows (BONUS /
        # SPLIT) before other same-day rows, then creation order.
        return order_ledger(
            Transaction.objects
            .filter(
                asset=asset,
                family=asset.family,
            )
        )

    # ==========================================================
    # POSITION
    # ==========================================================

    @staticmethod
    def calculate_position(asset):

        transactions = (
            HoldingCalculationEngine
            .get_transactions(asset)
        )

        # Corporate actions affect a holding only through accepted ledger
        # rows (BONUS / SPLIT / SELL / BUY); persisted CorporateAction events
        # are never overlaid here, so every engine agrees on the quantity.

        # The common transaction types used for position math can be
        # reduced directly in SQL. This removes the Python row-by-row
        # loop for the normal BUY/SIP-only path while preserving the
        # sell/bonus/split logic when those transaction types are present.
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

        # BUY/SIP add cost, SELL releases average cost, BONUS adds zero-cost
        # shares, SPLIT carries the signed quantity change (negative for a
        # reverse split, never below zero); other types do not move quantity.
        return replay_ledger(list(transactions))

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

        if effective_price["has_price"]:
            current_price = effective_price["price"]
            current_value = quantity * current_price
            unrealized_pnl = current_value - invested_value
        else:
            # No usable market price: carry the position at cost (value =
            # invested amount, P&L = 0) until a price is entered manually.
            current_price = average_cost
            current_value = invested_value
            unrealized_pnl = HoldingCalculationEngine.ZERO

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