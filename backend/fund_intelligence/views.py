from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from fund_intelligence.services.snapshot import build_fund_intelligence_snapshot


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def fund_intelligence_snapshot(request):
    """Multi Cap Regular Growth fund snapshot. Pass ``live=0`` to skip indianapi.in."""
    include_live = request.query_params.get("live", "1").strip().lower() not in {"0", "false", "no"}
    return Response(build_fund_intelligence_snapshot(include_live=include_live))
