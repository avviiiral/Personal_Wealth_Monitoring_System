"""Authenticated read-only API for global mutual-fund masterdata."""
from django.db.models import F, OuterRef, Q, Subquery
from rest_framework import serializers
from rest_framework.decorators import api_view, permission_classes
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from mutual_funds.master_models import MFMasterData


class MFMasterDataSerializer(serializers.ModelSerializer):
    class Meta:
        model = MFMasterData
        fields = "__all__"
        read_only_fields = tuple(field.name for field in MFMasterData._meta.fields)


class MFMasterDataPagination(PageNumberPagination):
    page_size = 50
    page_size_query_param = "page_size"
    max_page_size = 200


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def mutual_fund_masterdata(request):
    """Return one latest snapshot per scheme; this view never calls providers."""
    latest_date = (
        MFMasterData.objects.filter(scheme_id=OuterRef("scheme_id"))
        .order_by("-as_of_date")
        .values("as_of_date")[:1]
    )
    queryset = (
        MFMasterData.objects.annotate(_latest_snapshot_date=Subquery(latest_date))
        .filter(as_of_date=F("_latest_snapshot_date"))
        .order_by("scheme_name", "scheme_code")
    )

    category = (request.query_params.get("category") or "").strip()
    amc = (request.query_params.get("amc") or "").strip()
    plan = (request.query_params.get("plan") or "").strip()
    scheme_code = (request.query_params.get("scheme_code") or "").strip()
    aum_type = (request.query_params.get("aum_type") or "").strip()
    search = (request.query_params.get("q") or "").strip()

    if category:
        queryset = queryset.filter(sebi_category_level_3__iexact=category)
    if amc:
        queryset = queryset.filter(amc__icontains=amc)
    if plan:
        queryset = queryset.filter(plan__iexact=plan)
    if scheme_code:
        queryset = queryset.filter(scheme_code__iexact=scheme_code)
    if aum_type:
        queryset = queryset.filter(aum_type__iexact=aum_type)
    if search:
        queryset = queryset.filter(
            Q(scheme_name__icontains=search)
            | Q(scheme_code__icontains=search)
            | Q(amc__icontains=search)
            | Q(isin_growth__icontains=search)
            | Q(isin_idcw__icontains=search)
        )

    paginator = MFMasterDataPagination()
    page = paginator.paginate_queryset(queryset, request)
    payload = MFMasterDataSerializer(page, many=True).data
    return paginator.get_paginated_response(payload)
