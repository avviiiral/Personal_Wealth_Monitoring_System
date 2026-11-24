from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from mf_intelligence.models import MFHolding, MFHoldingSnapshot, MFScheme
from mutual_funds.models import MutualFundScheme, MutualFundUnderlying
from users.permissions import require_active_family

from .underlying_serializers import MutualFundUnderlyingSerializer


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def mutual_fund_underlying(request, scheme_id):
    family_id = require_active_family(request.user).id
    scheme = MutualFundScheme.objects.filter(
        id=scheme_id,
        family_id=family_id,
        is_active=True,
    ).first()
    if scheme is None:
        return Response({"detail": "Mutual fund scheme not found."}, status=404)

    portfolio_date = request.query_params.get("portfolio_date")
    queryset = MutualFundUnderlying.objects.filter(scheme=scheme)
    if portfolio_date:
        queryset = queryset.filter(portfolio_date=portfolio_date)
    else:
        latest_date = queryset.order_by("-portfolio_date").values_list("portfolio_date", flat=True).first()
        if latest_date:
            queryset = queryset.filter(portfolio_date=latest_date)

    local_rows = list(queryset.order_by("-percentage_of_nav", "security_name"))
    local_date = local_rows[0].portfolio_date if local_rows else None

    intelligence_scheme = None
    if scheme.scheme_code:
        intelligence_scheme = MFScheme.objects.filter(amfi_code=scheme.scheme_code).first()
    intelligence_snapshot = None
    if intelligence_scheme is not None:
        snapshots = MFHoldingSnapshot.objects.filter(scheme=intelligence_scheme)
        if portfolio_date:
            snapshots = snapshots.filter(as_of=portfolio_date)
        intelligence_snapshot = snapshots.order_by("-as_of").first()

    # Prefer the newer disclosure. On equal dates, keep the local record because
    # it may contain source-specific market values not held by MF Intelligence.
    if intelligence_snapshot is not None and (
        local_date is None or intelligence_snapshot.as_of > local_date
    ):
        holdings = MFHolding.objects.filter(snapshot=intelligence_snapshot).order_by(
            "-holding_pct", "name"
        )
        results = [
            {
                "id": item.id,
                "scheme": scheme.id,
                "scheme_name": scheme.scheme_name,
                "scheme_code": scheme.scheme_code,
                "security_name": item.name,
                "isin": item.isin or None,
                "quantity": item.quantity,
                "market_value": None,
                "percentage_of_nav": item.holding_pct,
                "sector": item.industry or None,
                "portfolio_date": intelligence_snapshot.as_of,
                "source": "MF_INTELLIGENCE",
                "source_reference": f"mf-intelligence:{intelligence_scheme.amfi_code}",
                "fetched_at": intelligence_snapshot.fetched_at,
                "holding_type": item.holding_type,
                "position_side": item.position_side,
                "change_pct": item.change_pct,
                "status": item.status,
            }
            for item in holdings
        ]
        return Response({
            "scheme_id": scheme.id,
            "scheme_name": scheme.scheme_name,
            "portfolio_date": intelligence_snapshot.as_of,
            "results": results,
        })

    return Response({
        "scheme_id": scheme.id,
        "scheme_name": scheme.scheme_name,
        "portfolio_date": local_date,
        "results": MutualFundUnderlyingSerializer(local_rows, many=True).data,
    })
