from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from mutual_funds.models import MutualFundScheme, MutualFundUnderlying
from users.permissions import get_visible_owner_ids

from .underlying_serializers import MutualFundUnderlyingSerializer


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def mutual_fund_underlying(request, scheme_id):
    visible_owner_ids = get_visible_owner_ids(request.user)
    scheme = MutualFundScheme.objects.filter(
        id=scheme_id,
        owner_id__in=visible_owner_ids,
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

    queryset = queryset.order_by("-percentage_of_nav", "security_name")
    return Response({
        "scheme_id": scheme.id,
        "scheme_name": scheme.scheme_name,
        "portfolio_date": queryset.first().portfolio_date if queryset.exists() else None,
        "results": MutualFundUnderlyingSerializer(queryset, many=True).data,
    })
