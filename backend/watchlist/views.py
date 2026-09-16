from django.db.models import Exists, OuterRef, Q, Subquery
from django.shortcuts import get_object_or_404
from rest_framework.decorators import api_view, permission_classes
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from investments.models import AssetCategory, PortfolioPosition
from users.permissions import get_visible_owner_ids
from watchlist.models import InvestmentProduct, PerformanceSnapshot, ProductType
from watchlist.serializers import PerformanceSnapshotSerializer, WatchListProductSerializer
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

    latest_snapshot = PerformanceSnapshot.objects.filter(
        product_id=OuterRef("pk")
    ).order_by("-date", "-id")
    ordering_fields = {
        "name": "name",
        "aum": "mutual_fund__aum",
        "1M": "latest_return_1m",
        "3M": "latest_return_3m",
        "6M": "latest_return_6m",
        "1Y": "latest_return_1y",
        "3Y": "latest_return_3y",
        "5Y": "latest_return_5y",
        "cagr": "latest_cagr",
    }
    queryset = queryset.annotate(
        latest_return_1m=Subquery(latest_snapshot.values("return_1m")[:1]),
        latest_return_3m=Subquery(latest_snapshot.values("return_3m")[:1]),
        latest_return_6m=Subquery(latest_snapshot.values("return_6m")[:1]),
        latest_return_1y=Subquery(latest_snapshot.values("return_1y")[:1]),
        latest_return_3y=Subquery(latest_snapshot.values("return_3y")[:1]),
        latest_return_5y=Subquery(latest_snapshot.values("return_5y")[:1]),
        latest_cagr=Subquery(latest_snapshot.values("cagr")[:1]),
    )
    ordering = params.get("ordering", "name")
    prefix = "-" if ordering.startswith("-") else ""
    key = ordering[1:] if prefix else ordering
    queryset = queryset.order_by(prefix + ordering_fields.get(key, "name"), "id")

    status = params.get("status", "").upper()
    if status in {"OWNED", "UNIVERSAL"}:
        owner_ids = get_visible_owner_ids(request.user)
        # Match OwnershipService semantics: use ISIN when the product has one;
        # otherwise fall back to external identifier/name.
        has_isin_match = Q(asset__isin__iexact=OuterRef("isin")) & ~Q(isin__isnull=True) & ~Q(isin="")
        fallback_match = (Q(asset__symbol__iexact=OuterRef("external_identifier")) | Q(asset__name__iexact=OuterRef("name"))) & (
            Q(isin__isnull=True) | Q(isin="")
        )
        owned_positions = PortfolioPosition.objects.filter(
            owner_id__in=owner_ids,
        ).filter(
            Q(quantity__gt=0) | Q(current_value__gt=0),
        ).filter(
            has_isin_match | fallback_match,
        )
        if product_type == ProductType.MUTUAL_FUND:
            owned_positions = owned_positions.filter(asset__category=AssetCategory.MUTUAL_FUND)
        queryset = queryset.annotate(has_owned_position=Exists(owned_positions))
        queryset = queryset.filter(has_owned_position=(status == "OWNED"))
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
