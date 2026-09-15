from django.db.models import Q

from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from investments.models import PortfolioPosition, Transaction
from users.permissions import get_visible_owner_ids


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def holding_report(request):
    """
    Return current portfolio positions for the holding report.

    This endpoint is deliberately based on PortfolioPosition rather than
    rebuilding the transaction-driven portfolio tree. That keeps the report
    fast and makes the downloaded report one row per current holding.
    """
    owner_ids = get_visible_owner_ids(request.user)

    positions = list(
        PortfolioPosition.objects
        .filter(
            owner_id__in=owner_ids,
            asset__is_active=True,
            quantity__gt=0,
        )
        .select_related("asset", "asset__security_master")
        .order_by("family_name", "portfolio", "asset__name")
    )

    asset_ids = {position.asset_id for position in positions}
    owner_id_set = {position.owner_id for position in positions}

    transactions = (
        Transaction.objects
        .filter(owner_id__in=owner_id_set, asset_id__in=asset_ids)
        .only(
            "owner_id",
            "asset_id",
            "family_name",
            "portfolio",
            "asset_class",
            "sub_class",
            "asset_name",
            "underlying",
            "advisors",
            "transaction_date",
            "id",
        )
        .order_by("owner_id", "asset_id", "family_name", "portfolio", "-transaction_date", "-id")
    )

    latest_metadata = {}
    for tx in transactions:
        key = (
            tx.owner_id,
            tx.asset_id,
            (tx.family_name or "").strip(),
            (tx.portfolio or "").strip(),
        )
        latest_metadata.setdefault(key, tx)

    def clean(value, default="Unassigned"):
        value = str(value or "").strip()
        return value or default

    results = []

    for position in positions:
        asset = position.asset
        security_master = getattr(asset, "security_master", None)
        key_prefix = (
            position.owner_id,
            position.asset_id,
            (position.family_name or "").strip(),
            (position.portfolio or "").strip(),
        )
        metadata = latest_metadata.get(key_prefix)

        results.append({
            "id": position.id,
            "owner_id": position.owner_id,
            "family_name": clean(position.family_name),
            "portfolio": clean(position.portfolio),
            "asset_class": clean(metadata.asset_class if metadata else None),
            "sub_class": clean(metadata.sub_class if metadata else None),
            "asset_id": asset.id,
            "asset_name": clean(metadata.asset_name if metadata else asset.name),
            "underlying": clean(metadata.underlying if metadata else None, ""),
            "isin": asset.isin,
            "advisors": clean(metadata.advisors if metadata else None, ""),
            "quantity": float(position.quantity or 0),
            "average_cost": float(position.average_cost or 0),
            "invested_value": float(position.invested_value or 0),
            "current_price": float(position.current_price or 0),
            "current_value": float(position.current_value or 0),
            "gain": float(position.gain or 0),
            "gain_percentage": (
                round(float(position.gain / position.invested_value * 100), 2)
                if position.invested_value
                else 0
            ),
            "xirr": None,
            "sector": security_master.sector if security_master else None,
            "cap_type": security_master.cap_type if security_master else None,
            "amc_name": security_master.amc_name if security_master else None,
        })

    return Response({
        "success": True,
        "count": len(results),
        "results": results,
    }, status=status.HTTP_200_OK)
