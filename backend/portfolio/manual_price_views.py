from datetime import date
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.utils import timezone

from rest_framework import status
from rest_framework.decorators import (
    api_view,
    permission_classes,
)
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from investments.models import Asset

from market_data.models import (
    DataSource,
    MarketPrice,
)

from portfolio.services.holding_engine import (
    HoldingCalculationEngine,
)

from portfolio.services.portfolio_position_engine import (
    PortfolioPositionEngine,
)

from users.permissions import (
    IsAdminOrSuperUser,
    get_visible_owner_ids,
)


@api_view(["PUT", "PATCH", "DELETE"])
@permission_classes([IsAuthenticated, IsAdminOrSuperUser])
def manual_asset_price(
    request,
    asset_id,
):
    """
    Create, update, or delete a manually entered
    current price for an asset.

    Manual prices are stored directly in MarketPrice
    using source=MANUAL so the entire portfolio
    calculation pipeline uses the same price source.

    This is intended for assets where automatic
    market data is unavailable or unreliable.

    An asset's underlying data is still stored against the user
    who originally created it (Asset.owner), but editability here
    follows the same family-shared visibility as everywhere else
    in PWMS (Dashboard/Portfolio/Analytics) - any Admin/Super
    User/System Owner who can SEE an asset (their own, or a
    fellow family member's, per their currently active family)
    can also edit its price. Role still gates WHO can edit at
    all; family membership gates WHICH assets.
    """

    # ==========================================================
    # FIND ASSET
    # ==========================================================

    try:
        asset = (
            Asset.objects
            .get(
                id=asset_id,
                owner__in=get_visible_owner_ids(request.user),
                is_active=True,
            )
        )

    except Asset.DoesNotExist:
        return Response(
            {
                "success": False,
                "message": "Asset not found.",
            },
            status=status.HTTP_404_NOT_FOUND,
        )

    # ==========================================================
    # DELETE MANUAL PRICE
    # ==========================================================

    if request.method == "DELETE":

        deleted, _ = (
            MarketPrice.objects
            .filter(
                asset=asset,
                source=DataSource.MANUAL,
            )
            .delete()
        )

        with transaction.atomic():

            holding = (
                HoldingCalculationEngine
                .rebuild_holding(
                    asset
                )
            )

            PortfolioPositionEngine.rebuild_all_for_user(
                # The actual data owner, not the editor - Transaction
                # rows (and therefore positions) are keyed by
                # Asset.owner, which may differ from request.user
                # now that a family member can edit another
                # member's asset.
                asset.owner
            )

        return Response(
            {
                "success": True,
                "message": (
                    "Manual price removed successfully."
                ),
                "deleted": bool(deleted),
                "data": {
                    "asset_id": asset.id,
                    "asset_name": asset.name,
                    "current_price": str(
                        holding.current_price
                    ),
                    "current_value": str(
                        holding.current_value
                    ),
                    "unrealized_pnl": str(
                        holding.unrealized_pnl
                    ),
                },
            },
            status=status.HTTP_200_OK,
        )

    # ==========================================================
    # GET PRICE
    # ==========================================================

    raw_price = request.data.get(
        "price"
    )

    if raw_price in (
        None,
        "",
    ):
        return Response(
            {
                "success": False,
                "message": "Price is required.",
            },
            status=status.HTTP_400_BAD_REQUEST,
        )

    # ==========================================================
    # VALIDATE PRICE
    # ==========================================================

    try:

        price = Decimal(
            str(raw_price)
            .replace(",", "")
            .replace("₹", "")
            .strip()
        )

    except (
        InvalidOperation,
        ValueError,
        TypeError,
    ):

        return Response(
            {
                "success": False,
                "message": (
                    "Price must be a valid number."
                ),
            },
            status=status.HTTP_400_BAD_REQUEST,
        )

    if price <= 0:

        return Response(
            {
                "success": False,
                "message": (
                    "Price must be greater than zero."
                ),
            },
            status=status.HTTP_400_BAD_REQUEST,
        )

    # ==========================================================
    # PRICE DATE
    # ==========================================================

    raw_price_date = request.data.get(
        "price_date"
    )

    if not raw_price_date:

        price_date = (
            timezone.localdate()
        )

    else:

        try:

            price_date = date.fromisoformat(
                str(raw_price_date)
            )

        except ValueError:

            return Response(
                {
                    "success": False,
                    "message": (
                        "price_date must be "
                        "YYYY-MM-DD."
                    ),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

    # ==========================================================
    # SAVE MANUAL MARKET PRICE
    # ==========================================================
    #
    # IMPORTANT:
    #
    # Older manual price entries for this asset are intentionally
    # KEPT, not deleted.
    #
    # get_current_price() / get_price_metadata() (portfolio_metrics.py)
    # already select the latest row by date, so keeping older rows
    # does not change the current/live price shown anywhere.
    #
    # But historical charts (HistoricalWealthAnalytics) rely on
    # having a real price-on-date series to look up. Deleting every
    # prior manual snapshot on each update meant only ONE data point
    # ever existed for manually-priced holdings (AIF, PMS, Commodity
    # ETFs, etc.) - so any historical window before that single date
    # had no price to look up and those holdings were silently
    # valued at ZERO for that period, producing a false "sudden
    # jump" on the Wealth Overview chart once that date was reached.
    #
    # Keeping each dated snapshot lets manual-price history
    # genuinely accumulate over time, the same way automatically
    # fetched prices do.

    with transaction.atomic():

        manual_price, _ = (
            MarketPrice.objects
            .update_or_create(
                asset=asset,
                date=price_date,
                source=DataSource.MANUAL,
                defaults={
                    "open_price": None,
                    "high_price": None,
                    "low_price": None,
                    "close_price": price,
                    "adjusted_close": price,
                    "volume": None,
                    "updated_by": request.user,
                },
            )
        )

        # ======================================================
        # REBUILD HOLDING
        # ======================================================

        holding = (
            HoldingCalculationEngine
            .rebuild_holding(
                asset
            )
        )

        # ======================================================
        # REBUILD PORTFOLIO POSITIONS
        # ======================================================

        PortfolioPositionEngine.rebuild_all_for_user(
            # See the matching comment in the DELETE branch above -
            # must be the asset's actual owner, not request.user.
            asset.owner
        )

    # ==========================================================
    # RESPONSE
    # ==========================================================

    return Response(
        {
            "success": True,
            "message": (
                "Manual price updated successfully."
            ),
            "data": {
                "asset_id": asset.id,
                "asset_name": asset.name,
                "price": str(
                    manual_price.close_price
                ),
                "price_date": str(
                    manual_price.date
                ),
                "current_price": str(
                    holding.current_price
                ),
                "current_value": str(
                    holding.current_value
                ),
                "unrealized_pnl": str(
                    holding.unrealized_pnl
                ),
            },
        },
        status=status.HTTP_200_OK,
    )