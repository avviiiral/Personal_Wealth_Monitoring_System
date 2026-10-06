from django.db import close_old_connections
import threading
from django.db.models import Exists, OuterRef, Q, Subquery
from django.shortcuts import get_object_or_404
from rest_framework.decorators import api_view, permission_classes
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from config.database_scheduler_lock import DATABASE_SCHEDULER_LOCK

from investments.models import PortfolioPosition, Transaction
from users.permissions import get_active_family_group_id, get_visible_owner_ids
from watchlist.models import InvestmentProduct, PerformanceSnapshot, ProductType, WatchListEntry
from watchlist.serializers import PerformanceSnapshotSerializer, WatchListProductSerializer
from watchlist.services.ownership import OwnershipService
from watchlist.services.pms import APMIPMSDiscoveryService
from watchlist.services.performance import AMFIPerformanceService
from watchlist.services.benchmark import BenchmarkPerformanceService
from watchlist.services.universe import AMFIUniverseService
from watchlist.services.amfi_history import prepare_mutual_fund_watchlist_history


class WatchListPagination(PageNumberPagination):
    page_size = 25
    page_size_query_param = "page_size"
    max_page_size = 100


def _watchlist_user_ids(request):
    """Return the users sharing the caller's active family's Watch List."""
    family_id = get_active_family_group_id(request.user)
    if family_id is None:
        return [request.user.id]

    from users.models import UserProfile

    member_ids = list(
        UserProfile.objects.filter(
            family_groups__id=family_id,
            user__is_active=True,
        ).values_list("user_id", flat=True)
    )
    return member_ids or [request.user.id]


def _filtered_products(request, product_type=None):
    queryset = InvestmentProduct.objects.filter(is_active=True).select_related("mutual_fund", "pms")

    # Presentation filter: a product must have at least one value that
    # can actually be displayed in the Watch List return/AUM columns.
    # Latest NAV alone is not sufficient because it would produce a row
    # where every visible metric is N/A.
    displayable_snapshot = PerformanceSnapshot.objects.filter(
        product_id=OuterRef("pk"),
    ).filter(
        Q(aum__isnull=False)
        | Q(return_1d__isnull=False)
        | Q(return_1w__isnull=False)
        | Q(return_1m__isnull=False)
        | Q(return_3m__isnull=False)
        | Q(return_6m__isnull=False)
        | Q(return_1y__isnull=False)
        | Q(return_3y__isnull=False)
        | Q(return_5y__isnull=False)
        | Q(return_since_inception__isnull=False)
        | Q(cagr__isnull=False)
    )
    # OWNED is an ownership view, so an owned product remains visible even
    # before its first performance snapshot has been imported.
    status = request.query_params.get("status", "").upper()
    if status != "OWNED":
        queryset = queryset.filter(Exists(displayable_snapshot))

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
        # Watch List is shared by every active member of the caller's
        # currently selected family.
        watchlist_entries = WatchListEntry.objects.filter(
            user_id__in=_watchlist_user_ids(request),
            product_id=OuterRef("pk"),
        )
        queryset = queryset.filter(Exists(watchlist_entries))
    elif status in {"OWNED", "UNIVERSAL"}:
        # Keep the status filter aligned with OwnershipService.bulk_enrich():
        # ownership is family-scoped, and the same Asset/PortfolioPosition
        # matching rules are used here. This fixes cases where a product is
        # shown as OWNED after an ISIN search but is excluded by status=OWNED.
        from users.permissions import family_scope

        active_position = Q(quantity__gt=0) | Q(current_value__gt=0)
        scoped_positions = family_scope(PortfolioPosition.objects, request.user).filter(active_position)

        # Ownership identity is product-type specific. Build one
        # correlated expression for PMS-name matching and one for MF-ISIN
        # matching so the unfiltered Watch List can contain both product types.
        pms_transactions = family_scope(Transaction.objects, request.user).filter(
            asset_name__iexact=OuterRef(OuterRef("name")),
            asset_id=OuterRef("asset_id"),
        )
        owned_pms_positions = scoped_positions.filter(Exists(pms_transactions))
        owned_pms_expression = Exists(owned_pms_positions)

        isin_positions = scoped_positions.filter(
            asset__isin__iexact=OuterRef("isin"),
        ).filter()
        owned_mf_expression = (
            ~Q(isin__isnull=True)
            & ~Q(isin="")
            & Exists(isin_positions)
        )

        # PMS uses strategy/Transaction.asset_name; Mutual Fund uses ISIN.
        # Do not let a Mutual Fund name match or a PMS ISIN accidentally mark
        # the product as owned.
        owned_expression = (
            (Q(product_type=ProductType.PMS) & owned_pms_expression)
            | (Q(product_type=ProductType.MUTUAL_FUND) & owned_mf_expression)
            | (
                ~Q(product_type__in=[ProductType.PMS, ProductType.MUTUAL_FUND])
                & (
                    (
                        ~Q(isin__isnull=True)
                        & ~Q(isin="")
                        & Exists(scoped_positions.filter(
                            asset__isin__iexact=OuterRef("isin"),
                        ))
                    )
                    | (
                        (Q(isin__isnull=True) | Q(isin=""))
                        & Exists(scoped_positions.filter(
                            Q(asset__symbol__iexact=OuterRef("external_identifier"))
                            | Q(asset__name__iexact=OuterRef("name"))
                        ))
                    )
                )
            )
        )
        queryset = queryset.filter(
            owned_expression if status == "OWNED" else ~owned_expression
        )
    return queryset


def _latest_snapshots(products):
    product_ids = [product.id for product in products]
    if not product_ids:
        return {}, {}

    snapshots = (
        PerformanceSnapshot.objects.filter(product_id__in=product_ids)
        .order_by("product_id", "-date", "-id")
    )
    latest = {}
    metric_snapshot = {}
    metric_fields = (
        "return_1d", "return_1w", "return_1m", "return_3m", "return_6m",
        "return_1y", "return_3y", "return_5y", "return_since_inception", "cagr",
    )
    for snapshot in snapshots:
        latest.setdefault(snapshot.product_id, snapshot)
        if snapshot.product_id not in metric_snapshot and any(
            getattr(snapshot, field) is not None for field in metric_fields
        ):
            metric_snapshot[snapshot.product_id] = snapshot
    return latest, metric_snapshot


def _watchlisted_ids(products, request):
    product_ids = [product.id for product in products]
    if not product_ids or not request.user.is_authenticated:
        return set()
    return set(
        WatchListEntry.objects.filter(
            user_id__in=_watchlist_user_ids(request),
            product_id__in=product_ids,
        ).values_list("product_id", flat=True)
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
    page = list(page or [])
    latest_snapshots, metric_snapshots = _latest_snapshots(page)
    ownership_cache = _ownership_cache(page, request)
    serializer = WatchListProductSerializer(
        page,
        many=True,
        context={
            "request": request,
            "latest_snapshots": latest_snapshots,
            "metric_snapshots": metric_snapshots,
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
    latest_snapshots, metric_snapshots = _latest_snapshots([product])
    ownership_cache = OwnershipService.bulk_enrich([product], request.user)
    return Response(
        WatchListProductSerializer(
            product,
            context={
                "request": request,
                "latest_snapshots": latest_snapshots,
                "metric_snapshots": metric_snapshots,
                "ownership_cache": ownership_cache,
                "watchlisted_ids": _watchlisted_ids([product], request),
            },
        ).data
    )


WATCH_LIST_REFRESH_LOCK = threading.Lock()
watch_list_refreshing = False


def _run_watch_list_refresh():
    global watch_list_refreshing
    close_old_connections()
    try:
        with DATABASE_SCHEDULER_LOCK:
            mf_result = AMFIUniverseService.refresh()
            # Populate the metrics used by the Watch List visibility filter
            # after the AMFI universe has been created/updated.
            mf_performance_result = AMFIPerformanceService.refresh()
            pms_result = APMIPMSDiscoveryService.refresh()
        return mf_result, mf_performance_result, pms_result
    finally:
        watch_list_refreshing = False
        WATCH_LIST_REFRESH_LOCK.release()
        close_old_connections()


BENCHMARK_CHOICES = ("BSE 500", "Nifty 50")


@api_view(["PATCH"])
@permission_classes([IsAuthenticated])
def watch_list_benchmark(request, product_id):
    product = get_object_or_404(
        InvestmentProduct.objects.select_related("mutual_fund", "pms"),
        pk=product_id,
        is_active=True,
    )
    benchmark = str(request.data.get("benchmark") or "").strip()
    if benchmark not in BENCHMARK_CHOICES:
        return Response(
            {"detail": "Benchmark must be one of: BSE 500, Nifty 50."},
            status=400,
        )

    if product.product_type == ProductType.MUTUAL_FUND:
        product.mutual_fund.benchmark = benchmark
        product.mutual_fund.save(update_fields=["benchmark"])
    elif product.product_type == ProductType.PMS:
        product.pms.benchmark = benchmark
        product.pms.save(update_fields=["benchmark"])
    else:
        return Response({"detail": "Benchmark is supported only for Mutual Fund and PMS products."}, status=400)

    return Response({"id": product.id, "benchmark": benchmark})


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def watch_list_benchmark_performance(request, product_id):
    product = get_object_or_404(
        InvestmentProduct.objects.select_related("mutual_fund", "pms"),
        pk=product_id,
        is_active=True,
    )
    chart_period = request.query_params.get("period", "1Y").upper()
    if chart_period not in BenchmarkPerformanceService.PERIOD_DAYS:
        chart_period = "1Y"

    # Older Watch List entries may only have the six return-anchor snapshots
    # (1M/3M/6M/1Y/3Y/5Y), which makes the indexed chart appear as a handful
    # of straight line segments. Ensure the selected chart period has dense
    # AMFI master NAV history before calculating the aligned series.
    if product.product_type == ProductType.MUTUAL_FUND:
        prepare_mutual_fund_watchlist_history(
            product,
            days=BenchmarkPerformanceService.PERIOD_DAYS[chart_period],
        )

    result = BenchmarkPerformanceService.calculate(product, chart_period=chart_period)
    if result is None:
        return Response({"available": False, "benchmark": None, "message": "Select a supported benchmark first."})
    return Response(result)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def watch_list_benchmarks_performance(request):
    results = []
    for benchmark in ("Nifty 50", "BSE 500"):
        results.append(BenchmarkPerformanceService.calculate_benchmark(benchmark))
    return Response({"benchmarks": results})


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
    """Add or remove a product from the caller's personal Watch List."""
    product = get_object_or_404(InvestmentProduct, pk=product_id, is_active=True)
    family_user_ids = _watchlist_user_ids(request)
    entries = WatchListEntry.objects.filter(
        user_id__in=family_user_ids,
        product=product,
    )
    if entries.exists():
        entries.delete()
        return Response({"id": product.id, "is_watchlisted": False})
    WatchListEntry.objects.bulk_create(
        [
            WatchListEntry(user_id=user_id, product=product)
            for user_id in family_user_ids
        ],
        ignore_conflicts=True,
    )

    if product.product_type == ProductType.MUTUAL_FUND:
        prepare_mutual_fund_watchlist_history(product)

    return Response({"id": product.id, "is_watchlisted": True})


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def watch_list_bulk_add(request):
    """Add multiple active mutual-fund/PMS products to the caller's Watch List."""
    product_ids = request.data.get("product_ids", [])
    if not isinstance(product_ids, list) or not product_ids:
        return Response({"detail": "product_ids must be a non-empty list."}, status=400)

    try:
        product_ids = list({int(product_id) for product_id in product_ids})
    except (TypeError, ValueError):
        return Response({"detail": "product_ids must contain valid product IDs."}, status=400)

    products = InvestmentProduct.objects.filter(
        id__in=product_ids,
        is_active=True,
        product_type__in=[ProductType.MUTUAL_FUND, ProductType.PMS],
    )
    valid_ids = set(products.values_list("id", flat=True))
    if not valid_ids:
        return Response({"detail": "No valid products were selected."}, status=400)

    family_user_ids = _watchlist_user_ids(request)
    existing_ids = set(
        WatchListEntry.objects.filter(
            user_id__in=family_user_ids,
            product_id__in=valid_ids,
        ).values_list("product_id", flat=True)
    )
    WatchListEntry.objects.bulk_create(
        [
            WatchListEntry(user_id=user_id, product_id=product_id)
            for user_id in family_user_ids
            for product_id in valid_ids - existing_ids
        ],
        ignore_conflicts=True,
    )

    new_mutual_funds = products.filter(
        product_type=ProductType.MUTUAL_FUND,
        id__in=valid_ids - existing_ids,
    ).select_related("mutual_fund")
    for product in new_mutual_funds:
        prepare_mutual_fund_watchlist_history(product)

    return Response({
        "selected": len(valid_ids),
        "added": len(valid_ids - existing_ids),
        "already_watchlisted": len(existing_ids),
    })


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def watch_list_bulk_remove(request):
    """Remove multiple products from the caller's personal Watch List."""
    product_ids = request.data.get("product_ids", [])
    if not isinstance(product_ids, list) or not product_ids:
        return Response({"detail": "product_ids must be a non-empty list."}, status=400)

    try:
        product_ids = list({int(product_id) for product_id in product_ids})
    except (TypeError, ValueError):
        return Response({"detail": "product_ids must contain valid product IDs."}, status=400)

    deleted_count, _ = WatchListEntry.objects.filter(
        user_id__in=_watchlist_user_ids(request),
        product_id__in=product_ids,
    ).delete()
    return Response({
        "selected": len(product_ids),
        "removed": deleted_count,
    })


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def watch_list_refresh(request):
    global watch_list_refreshing

    # The universe refresh can take tens of seconds. Run it outside the
    # request so the Watch List is never blocked by a full universe import.
    if not WATCH_LIST_REFRESH_LOCK.acquire(blocking=False):
        return Response({"status": "already_running"}, status=202)

    watch_list_refreshing = True
    threading.Thread(
        target=_run_watch_list_refresh,
        name="watch-list-universe-refresh",
        daemon=True,
    ).start()

    return Response({"status": "started"}, status=202)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def watch_list_refresh_status(request):
    return Response({"refreshing": watch_list_refreshing})


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def prepare_watch_list(request):
    if not request.user.is_staff:
        return Response({"detail": "Staff permission required."}, status=403)

    days = int(request.data.get("days", 7))
    dry_run = str(request.data.get("dry_run", "")).lower() in {"1", "true", "yes"}
    result = {
        "amfi": AMFIUniverseService.prepare_watchlist_history(days=days, dry_run=dry_run),
        "pms": APMIPMSDiscoveryService.prepare_watchlist_history(days=days, dry_run=dry_run),
    }
    return Response(result)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def prepare_watch_list_benchmark(request):
    if not request.user.is_staff:
        return Response({"detail": "Staff permission required."}, status=403)

    days = int(request.data.get("days", 7))
    dry_run = str(request.data.get("dry_run", "")).lower() in {"1", "true", "yes"}
    with DATABASE_SCHEDULER_LOCK:
        result = BenchmarkPerformanceService.prepare_benchmark_history(days=days, dry_run=dry_run)
    return Response(result)
