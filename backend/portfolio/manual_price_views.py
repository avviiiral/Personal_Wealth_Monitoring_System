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

from users.permissions import get_visible_owner_ids
from users.permissions import is_admin_or_above


@api_view(["PUT", "PATCH", "DELETE"])
@permission_classes([IsAuthenticated])
def manual_asset_price(
    request,
    asset_id,
):
    """
    Create, update, or delete a manually entered
    current price for an asset.

    Manual prices use the same MarketPrice pipeline as
    automatic prices, while preserving dated manual
    snapshots for historical analytics.

    Editability is role-based (Admin/Super User/System Owner)
    and asset visibility remains family/owner based. The
    position rebuild is driven by the asset's family, not by
    the editor's active-family selection, because the editor
    may be a different family member.
    """

    # ==========================================================
    # AUTHORIZE CAPABILITY
    # ==========================================================

    if not is_admin_or_above(request.user):
        return Response(
            {
                "success": False,
                "message": "This action requires Admin, Super User, or System Owner privileges.",
            },
            status=status.HTTP_403_FORBIDDEN,
        )

    # ==========================================================
    # FIND ASSET
    # ==========================================================

    visible_owner_ids = get_visible_owner_ids(request.user)

    asset = (
        Asset.objects
        .filter(
            id=asset_id,
            is_active=True,
        )
        .filter(
            family_id__in=request.user.profile.family_groups.values_list(
                "id",
                flat=True,
            )
        )
        .first()
    )

    if asset is None:
        asset = (
            Asset.objects
            .filter(
                id=asset_id,
                owner_id__in=visible_owner_ids,
                is_active=True,
            )
            .first()
        )

    if asset is None:
        return Response(
            {
                "success": False,
                "message": "Asset not found.",
            },
            status=status.HTTP_404_NOT_FOUND,
        )

    # Legacy assets without a family cannot participate in
    # PortfolioPosition rows because positions are family-owned.
    # Manual price editing itself remains available; the holding
    # rebuild below still works for the asset.
    
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
            holding = HoldingCalculationEngine.rebuild_holding(asset)

            if asset.family_id is not None:
                PortfolioPositionEngine.rebuild_all_for_family(
                    asset.family
                )

        return Response(
            {
                "success": True,
                "message": "Manual price removed successfully.",
                "deleted": bool(deleted),
                "data": {
                    "asset_id": asset.id,
                    "asset_name": asset.name,
                    "current_price": str(holding.current_price),
                    "current_value": str(holding.current_value),
                    "unrealized_pnl": str(holding.unrealized_pnl),
                },
            },
            status=status.HTTP_200_OK,
        )

    raw_price = request.data.get("price")

    if raw_price in (None, ""):
        return Response(
            {
                "success": False,
                "message": "Price is required.",
            },
            status=status.HTTP_400_BAD_REQUEST,
        )

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
                "message": "Price must be a valid number.",
            },
            status=status.HTTP_400_BAD_REQUEST,
        )

    if price <= 0:
        return Response(
            {
                "success": False,
                "message": "Price must be greater than zero.",
            },
            status=status.HTTP_400_BAD_REQUEST,
        )

    raw_price_date = request.data.get("price_date")

    if not raw_price_date:
        price_date = timezone.localdate()
    else:
        try:
            price_date = date.fromisoformat(str(raw_price_date))
        except ValueError:
            return Response(
                {
                    "success": False,
                    "message": "price_date must be YYYY-MM-DD.",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

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

        holding = HoldingCalculationEngine.rebuild_holding(asset)

        if asset.family_id is not None:
            PortfolioPositionEngine.rebuild_all_for_family(
                asset.family
            )

    return Response(
        {
            "success": True,
            "message": "Manual price updated successfully.",
            "data": {
                "asset_id": asset.id,
                "asset_name": asset.name,
                "price": str(manual_price.close_price),
                "price_date": str(manual_price.date),
                "current_price": str(holding.current_price),
                "current_value": str(holding.current_value),
                "unrealized_pnl": str(holding.unrealized_pnl),
            },
        },
        status=status.HTTP_200_OK,
    )
