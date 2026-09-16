from django.db.models import Q
from django.shortcuts import get_object_or_404
from rest_framework.decorators import api_view, permission_classes
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from watchlist.models import InvestmentProduct, PerformanceSnapshot, ProductType
from watchlist.serializers import PerformanceSnapshotSerializer, WatchListProductSerializer
from watchlist.services.ownership import OwnershipService
from watchlist.services.universe import AMFIUniverseService, PMSDiscoveryService


class WatchListPagination(PageNumberPagination):
    page_size = 25
    page_size_query_param = "page_size"
    max_page_size = 100


def _filtered_products(request, product_type=None):
    queryset = InvestmentProduct.objects.filter(is_active=True).select_related("mutual_fund", "pms")
    if product_type:
        queryset = queryset.filter(product_type=product_type)
    params = request.query_params
    search = params.get("search")
    if search:
        queryset = queryset.filter(
            Q(name__icontains=search) | Q(provider__icontains=search) | Q(isin__icontains=search)
        )
    for field in ("provider", "country", "category", "sub_category", "isin"):
        value = params.get(field)
        if value:
            queryset = queryset.filter(**{f"{field}__icontains": value})
    mf_fields = {"asset_class": "mutual_fund__fund_type", "plan": "mutual_fund__plan", "option": "mutual_fund__option"}
    for query_field, model_field in mf_fields.items():
        value = params.get(query_field)
        if value and product_type == ProductType.MUTUAL_FUND:
            queryset = queryset.filter(**{f"{model_field}__icontains": value})
    ordering = params.get("ordering", "name")
    allowed = {
        "name": "name", "aum": "mutual_fund__aum", "1M": "performance_snapshots__return_1m",
        "3M": "performance_snapshots__return_3m", "6M": "performance_snapshots__return_6m",
        "1Y": "performance_snapshots__return_1y", "3Y": "performance_snapshots__return_3y",
        "5Y": "performance_snapshots__return_5y", "cagr": "performance_snapshots__cagr",
    }
    prefix = "-" if ordering.startswith("-") else ""
    key = ordering[1:] if prefix else ordering
    queryset = queryset.order_by(prefix + allowed.get(key, "name"), "id").distinct()
    status = params.get("status", "").upper()
    if status in {"OWNED", "UNIVERSAL"}:
        ids = []
        for product in queryset:
            if OwnershipService.enrich(product, request.user)["status"] == status:
                ids.append(product.id)
        queryset = queryset.filter(id__in=ids)
    return queryset


def _latest_snapshots(products):
    """Load one latest performance snapshot per page product in a single query."""
    product_ids = [product.id for product in products]
    if not product_ids:
        return {}
    snapshots = (
        PerformanceSnapshot.objects.filter(product_id__in=product_ids)
        .order_by("product_id", "-date", "-id")
    )
    latest = {}
    for snapshot in snapshots:
        latest.setdefault(snapshot.product_id, snapshot)
    return latest


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def watch_list_products(request):
    product_type = request.query_params.get("product_type")
    if product_type:
        product_type = product_type.upper()
    queryset = _filtered_products(request, product_type if product_type in ProductType.values else None)
    paginator = WatchListPagination()
    page = paginator.paginate_queryset(queryset, request)
    latest_snapshots = _latest_snapshots(page)
    serializer = WatchListProductSerializer(
        page,
        many=True,
        context={"request": request, "latest_snapshots": latest_snapshots},
    )
    return paginator.get_paginated_response(serializer.data)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def watch_list_product_detail(request, product_id):
    product = get_object_or_404(
        InvestmentProduct.objects.select_related("mutual_fund", "pms"),
        pk=product_id,
        is_active=True,
    )
    latest_snapshots = _latest_snapshots([product])
    return Response(
        WatchListProductSerializer(
            product,
            context={"request": request, "latest_snapshots": latest_snapshots},
        ).data
    )


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def watch_list_performance(request, product_id):
    product = get_object_or_404(InvestmentProduct, pk=product_id, is_active=True)
    snapshots = product.performance_snapshots.order_by("-date")
    paginator = WatchListPagination()
    page = paginator.paginate_queryset(snapshots, request)
    serializer = PerformanceSnapshotSerializer(page, many=True)
    return paginator.get_paginated_response(serializer.data)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def watch_list_refresh(request):
    mf_result = AMFIUniverseService.refresh()
    pms_result = PMSDiscoveryService.refresh()
    return Response({"mutual_funds": mf_result, "pms": pms_result})
