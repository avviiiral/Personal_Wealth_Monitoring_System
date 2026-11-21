from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .models import AMFIMasterScheme


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def sebi_category_funds(request):
    """
    Return cached SEBI Growth scheme categories and schemes for the selected
    category. This endpoint reads the local AMFI master only; it never calls
    MFapi, IndianAPI, or another external provider.
    """
    universe = AMFIMasterScheme.objects.filter(
        is_active=True,
        sebi_universe_active=True,
        is_growth_plan=True,
    ).exclude(level_3_category="")

    categories = list(
        universe.order_by("level_3_category")
        .values_list("level_3_category", flat=True)
        .distinct()
    )

    selected_category = (request.query_params.get("category") or "").strip()
    funds = []
    if selected_category:
        funds = list(
            universe.filter(level_3_category=selected_category)
            .order_by("scheme_name")
            .values(
                "scheme_code",
                "scheme_name",
                "amc_name",
                "plan",
                "option",
                "level_1_branch",
                "level_2_group",
                "level_3_category",
            )
        )

    return Response({
        "categories": categories,
        "selected_category": selected_category or None,
        "fund_count": len(funds),
        "funds": funds,
    })
