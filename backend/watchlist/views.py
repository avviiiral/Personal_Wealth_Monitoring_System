from django.db.models import Exists, OuterRef, Q, Subquery
from django.shortcuts import get_object_or_404
from rest_framework.decorators import api_view, permission_classes
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from investments.models import AssetCategory, PortfolioPosition
from users.permissions import get_visible_owner_ids
from watchlist.models import InvestmentProduct, PerformanceSnapshot, ProductType, WatchListEntry
from watchlist.serializers import PerformanceSnapshotSerializer, WatchListProductSerializer
from watchlist.services.ownership import OwnershipService
from watchlist.services.pms import APMIPMSDiscoveryService
from watchlist.services.universe import AMFIUniverseService


class WatchListPagination(PageNumberPagination):
    page_size = 25
    page_size_query_param = "page_size"
    max_page_size = 100


def _filtered_products(request, product_type=None):
    queryset = InvestmentProduct.objects.filter(is_active=True).select_related("mutual_fund", "pms")
    if product_type:
        queryset = queryset.filter(product_type=product_type)
    params = request.query_params
    search = params.get("search", "").strip()
    if search:
        queryset = queryset.filter(
            Q(name__icontains=search)
            | Q(provider__icontains=search)
            | Q(isin__icontains=search)
            | Q(category__icontains=search)
            | Q(sub_category__icontains=search)
            | Q(external_identifier__icontains=search)
        )

    # Provider and category values come directly from the dropdown options,
    # so use exact matches instead of substring scans. Provider already has
    # a product_type/provider index and category has a dedicated composite index.
    for field in ("provider", "category"):
        value = params.get(field)
        if value:
            queryset = queryset.filter(**{field: value})
    for field in ("country", "sub_category", "isin"):
        value = params.get(field)
        if value:
            queryset = queryset.filter(**{f"{field}__icontains": value})

    mf_fields = {"asset_class": "mutual_fund__fund_type", "plan": "mutual_fund__plan", "option": "mutual_fund__option"}
    for query_field, model_field in mf_fields.items():
        value = params.get(query_field)
        if value and product_type == ProductType.MUTUAL_FUND:
            queryset = queryset.filter(**{f"{model_field}__icontains": value})

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
    ordering = params.get("ordering", "name")
    prefix = "-" if ordering.startswith("-") else ""
    key = ordering[1:] if prefix else ordering

    # Only calculate a correlated snapshot subquery when the user is
    # actually sorting by a performance metric. Normal search/filter/name
    # sorting no longer pays for seven snapshot subqueries per product.
    ordering_field = ordering_fields.get(key, "name")
    if ordering_field.startswith("latest_"):
        latest_snapshot = PerformanceSnapshot.objects.filter(
            product_id=OuterRef("pk")
        ).order_by("-date", "-id")
        metric_field = ordering_field.removeprefix("latest_")
        queryset = queryset.annotate(
            **{ordering_field: Subquery(latest_snapshot.values(metric_field)[:1])}
        )

    queryset = queryset.order_by(prefix + ordering_field, "id")

    status = params.get("status", "").upper()
    if status == "WATCHLIST":
        watchlist_entries = WatchListEntry.objects.filter(user=request.user, product_id=OuterRef("pk"))
        queryset = queryset.filter(Exists(watchlist_entries))
    elif status in {"OWNED", "UNIVERSAL"}:
        owner_ids = get_visible_owner_ids(request.user)
        active_position = Q(quantity__gt=0) | Q(current_value__gt=0)
        isin_positions = PortfolioPosition.objects.filter(
            owner_id__in=owner_ids,
        ).filter(active_position).filter(asset__isin__iexact=OuterRef("isin"))
        fallback_positions = PortfolioPosition.objects.filter(
            owner_id__in=owner_ids,
        ).filter(active_position).filter(
            Q(asset__symbol__iexact=OuterRef("external_identifier"))
            | Q(asset__name__iexact=OuterRef("name"))
        )

        # PMS ownership is dynamic: match the portfolio Asset name against
        # the InvestmentProduct name populated from the APMI PMS strategy.
        # No PMS names are hardcoded and IAID is not used for ownership.
        if product_type == ProductType.PMS:
            pms_name_positions = PortfolioPosition.objects.filter(
                owner_id__in=owner_ids,
            ).filter(active_position).filter(
                asset__name__iexact=OuterRef("name")
            )
        else:
            pms_name_positions = PortfolioPosition.objects.none()

        if product_type == ProductType.MUTUAL_FUND:
            isin_positions = isin_positions.filter(asset__category=AssetCategory.MUTUAL_FUND)
            fallback_positions = fallback_positions.filter(asset__category=AssetCategory.MUTUAL_FUND)

        owned_expression = (
            (~Q(isin__isnull=True) & ~Q(isin="") & Q(has_owned_isin=True))
            | (Q(isin__isnull=True) | Q(isin="")) & Q(has_owned_fallback=True)
            | (Q(product_type=ProductType.PMS) & Q(has_owned_pms_name=True))
        )
        queryset = queryset.annotate(
            has_owned_isin=Exists(isin_positions),
            has_owned_fallback=Exists(fallback_positions),
            has_owned_pms_name=Exists(pms_name_positions),
        ).filter(owned_expression if status == "OWNED" else ~owned_expression)
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


def _watchlisted_ids(products, request):
    product_ids = [product.id for product in products]
    if not product_ids or not request.user.is_authenticated:
        return set()
    return set(
        WatchListEntry.objects.filter(user=request.user, product_id__in=product_ids).values_list("product_id", flat=True)
    )


def _ownership_cache(products, request):
    status = request.query_params.get("status", "").upper()
    if status == "UNIVERSAL":
        return {
            product.id: {
                "status": "UNIVERSAL",
                "ownership": [],
                "owned_current_value": 0,
                "owned_invested_value": 0,
            }
            for product in products
        }
    return OwnershipService.bulk_enrich(products, request.user)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def watch_list_filters(request):
    product_type = request.query_params.get("product_type", "").upper()
    queryset = InvestmentProduct.objects.filter(is_active=True)
    if product_type in ProductType.values:
        queryset = queryset.filter(product_type=product_type)

    providers = list(
        queryset.exclude(provider__isnull=True)
        .exclude(provider="")
        .values_list("provider", flat=True)
        .distinct()
        .order_by("provider")
    )
    categories = list(
        queryset.exclude(category__isnull=True)
        .exclude(category="")
        .values_list("category", flat=True)
        .distinct()
        .order_by("category")
    )
    return Response({"providers": providers, "categories": categories})


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def watch_list_products(request):
    product_type = request.query_params.get("product_type")
    if product_type:
        product_type = product_type.upper()
    queryset = _filtered_products(request, product_type if product_type in ProductType.values else None)
    paginator = WatchListPagination()
    page = paginator.paginate_queryset(queryset, request)
    page = list(page)
    latest_snapshots = _latest_snapshots(page)
    ownership_cache = _ownership_cache(page, request)
    serializer = WatchListProductSerializer(
        page,
        many=True,
        context={
            "request": request,
            "latest_snapshots": latest_snapshots,
            "ownership_cache": ownership_cache,
            "watchlisted_ids": _watchlisted_ids(page, request),
        },
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
    ownership_cache = OwnershipService.bulk_enrich([product], request.user)
    return Response(
        WatchListProductSerializer(
            product,
            context={
                "request": request,
                "latest_snapshots": latest_snapshots,
                "ownership_cache": ownership_cache,
                "watchlisted_ids": _watchlisted_ids([product], request),
            },
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
def watch_list_toggle(request, product_id):
    """Add or remove a product from the caller's personal Watch List (the checkmark toggle)."""
    product = get_object_or_404(InvestmentProduct, pk=product_id, is_active=True)
    entry = WatchListEntry.objects.filter(user=request.user, product=product).first()
    if entry:
        entry.delete()
        return Response({"id": product.id, "is_watchlisted": False})
    WatchListEntry.objects.create(user=request.user, product=product)
    return Response({"id": product.id, "is_watchlisted": True})


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def watch_list_refresh(request):
    mf_result = AMFIUniverseService.refresh()
    pms_result = APMIPMSDiscoveryService.refresh()
    return Response({"mutual_funds": mf_result, "pms": pms_result})
