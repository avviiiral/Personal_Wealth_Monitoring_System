from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from investments.models import Asset

from portfolio.services.holding_engine import HoldingCalculationEngine

from users.permissions import get_visible_owner_ids


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def settings_price_list(request):
    """
    GET /api/settings/prices/

    Lists the assets the authenticated user can currently see -
    their own, plus any fellow member's within their active
    family (the same family-shared visibility used by Dashboard/
    Portfolio/Analytics; System Owner sees every asset) - with
    each one's current effective price, clearly distinguishing an
    automatic quote from a manual override.

    Viewers can see this (view-only); editing still requires
    Admin/Super User/System Owner and goes through the existing
    `PUT/PATCH /api/portfolio/assets/<id>/manual-price/` endpoint,
    also exposed at `/api/settings/prices/<id>/` for the same
    view function.
    """

    assets = (
        Asset.objects
        .filter(owner__in=get_visible_owner_ids(request.user), is_active=True)
        .order_by("name")
    )

    results = []

    for asset in assets:
        effective = HoldingCalculationEngine.get_effective_price(asset)

        manual_record = None

        if effective["is_manual"]:
            from market_data.models import DataSource, MarketPrice

            manual_record = (
                MarketPrice.objects
                .filter(asset=asset, source=DataSource.MANUAL)
                .order_by("-date", "-id")
                .first()
            )

        results.append(
            {
                "asset_id": asset.id,
                "asset_name": asset.name,
                "category": asset.category,
                "currency": asset.currency,
                "price": (str(effective["price"]) if effective["price"] is not None else None),
                "price_date": effective["date"],
                "price_source": effective["source"],
                "manual_override_enabled": effective["is_manual"],
                "updated_by": (
                    manual_record.updated_by.username
                    if manual_record and manual_record.updated_by
                    else None
                ),
                "updated_at": (manual_record.created_at if manual_record else None),
            }
        )

    return Response({"results": results})
