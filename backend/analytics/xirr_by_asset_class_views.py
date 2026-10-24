from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .services.xirr_by_asset_class import XIRRByAssetClassService


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def wealth_xirr_by_asset_class(request):
    family_name = request.GET.get("family") or None
    results = XIRRByAssetClassService.calculate(
        request.user,
        family_name=family_name,
    )
    return Response({"results": results})
