"""Authenticated, database-backed APIs for the Mutual Fund Intelligence page."""
from django.db.models import Count, F, OuterRef, Q, Subquery
from django.http import Http404
from rest_framework import serializers
from rest_framework.decorators import api_view, permission_classes
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status

from mutual_funds.models import AMFIMasterNAV, AMFIMasterScheme
from mutual_funds.master_models import (
    MFMasterData,
    MFMasterHolding,
    MFProviderBenchmark,
    MFProviderBenchmarkObservation,
    MFSourceRun,
)
from mutual_funds.services.charliealpha import CharlieAlphaService, NAV_SOURCE, SOURCE


class IntelligenceSnapshotSerializer(serializers.ModelSerializer):
    class Meta:
        model = MFMasterData
        fields = "__all__"
        read_only_fields = tuple(field.name for field in MFMasterData._meta.fields)


class IntelligencePagination(PageNumberPagination):
    page_size = 50
    page_size_query_param = "page_size"
    max_page_size = 200


def _charliealpha_snapshot_filter():
    return (
        Q(field_sources__nav__source=SOURCE)
        | Q(field_sources__performance_and_risk__source=SOURCE)
        | Q(field_sources__ter__source=SOURCE)
        | Q(field_sources__aum__source=SOURCE)
        | Q(field_sources__holdings__source=SOURCE)
    )


def _latest_snapshots():
    provider_rows = MFMasterData.objects.filter(_charliealpha_snapshot_filter())
    latest_date = (
        provider_rows.filter(scheme_id=OuterRef("scheme_id"))
        .order_by("-as_of_date")
        .values("as_of_date")[:1]
    )
    return (
        provider_rows.annotate(_latest_snapshot_date=Subquery(latest_date))
        .filter(as_of_date=F("_latest_snapshot_date"))
        .select_related("scheme")
    )


def _source_status():
    latest = {}
    for run in MFSourceRun.objects.filter(source=SOURCE).order_by("-started_at")[:200]:
        key = run.command
        if key not in latest:
            latest[key] = {
                "domain": key,
                "status": run.status,
                "started_at": run.started_at,
                "completed_at": run.completed_at,
                "records_fetched": run.rows_parsed,
                "inserted": run.rows_inserted,
                "updated": run.rows_updated,
                "skipped": run.rows_skipped,
                "failed": run.rows_failed,
                "unmatched": run.rows_unmatched,
            }
    return list(latest.values())


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def charliealpha_overview(request):
    """Compact inventory, coverage and latest-snapshot universe for the UI."""
    queryset = _latest_snapshots().order_by("scheme_name", "scheme_code")
    search = (request.query_params.get("q") or "").strip()
    amc = (request.query_params.get("amc") or "").strip()
    category = (request.query_params.get("category") or "").strip()
    plan = (request.query_params.get("plan") or "").strip()
    option = (request.query_params.get("option") or "").strip()

    if search:
        queryset = queryset.filter(
            Q(scheme_name__icontains=search)
            | Q(scheme_code__icontains=search)
            | Q(isin_growth__icontains=search)
            | Q(isin_idcw__icontains=search)
            | Q(amc__icontains=search)
        )
    if amc:
        queryset = queryset.filter(amc__icontains=amc)
    if category:
        queryset = queryset.filter(
            Q(sebi_category_level_3__iexact=category)
            | Q(sebi_category_level_2__iexact=category)
            | Q(sebi_category_level_1__iexact=category)
        )
    if plan:
        queryset = queryset.filter(plan__iexact=plan)
    if option:
        queryset = queryset.filter(option__iexact=option)

    pagination = IntelligencePagination()
    page = pagination.paginate_queryset(queryset, request)
    results = IntelligenceSnapshotSerializer(page, many=True).data

    latest = _latest_snapshots()
    latest_date = latest.order_by("-as_of_date").values_list("as_of_date", flat=True).first()
    nav_coverage = AMFIMasterNAV.objects.filter(source=NAV_SOURCE).values("scheme_id").distinct().count()
    holdings_coverage = MFMasterHolding.objects.filter(source=SOURCE).values("scheme_id").distinct().count()
    metric_coverage = (
        MFSourceRun.objects.filter(source=SOURCE, command="metrics", status__in=("SUCCESS", "PARTIAL"))
        .values("metadata__scheme_code").distinct().count()
    )
    summary = {
        "active_amfi_schemes": AMFIMasterScheme.objects.filter(is_active=True).count(),
        "schemes_with_masterdata": latest.count(),
        "amc_coverage": latest.exclude(amc="").values("amc").distinct().count(),
        "category_coverage": latest.exclude(sebi_category_level_3="").values("sebi_category_level_3").distinct().count(),
        "nav_coverage": nav_coverage,
        "metrics_coverage": metric_coverage,
        "holdings_coverage": holdings_coverage,
        "latest_snapshot_date": latest_date,
        "provider": SOURCE,
    }
    filters = {
        "amcs": list(latest.exclude(amc="").values_list("amc", flat=True).distinct().order_by("amc")),
        "categories": list(latest.exclude(sebi_category_level_3="").values_list("sebi_category_level_3", flat=True).distinct().order_by("sebi_category_level_3")),
        "plans": list(latest.exclude(plan="").values_list("plan", flat=True).distinct().order_by("plan")),
        "options": list(latest.exclude(option="").values_list("option", flat=True).distinct().order_by("option")),
    }
    payload = {
        "summary": summary,
        "filters": filters,
        "data_sources": _source_status(),
        "results": results,
    }
    return pagination.get_paginated_response(payload)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def charliealpha_fund_detail(request, scheme_code: str):
    scheme = AMFIMasterScheme.objects.filter(scheme_code=scheme_code, is_active=True).first()
    if scheme is None:
        raise Http404("Mutual-fund scheme was not found in the AMFI master.")
    snapshot = _latest_snapshots().filter(scheme=scheme).first()
    nav_limit = min(5000, max(30, int(request.query_params.get("nav_limit", 1000))))
    nav_queryset = AMFIMasterNAV.objects.filter(scheme=scheme, source=NAV_SOURCE)
    from_date = request.query_params.get("from_date")
    to_date = request.query_params.get("to_date")
    if from_date:
        nav_queryset = nav_queryset.filter(date__gte=from_date)
    if to_date:
        nav_queryset = nav_queryset.filter(date__lte=to_date)
    nav_rows = list(nav_queryset.order_by("-date")[:nav_limit])
    holdings_queryset = MFMasterHolding.objects.filter(scheme=scheme, source=SOURCE)
    requested_holding_date = (request.query_params.get("holdings_as_of") or "").strip()
    if requested_holding_date:
        holdings_queryset = holdings_queryset.filter(portfolio_date=requested_holding_date)
    holding_date = holdings_queryset.order_by("-portfolio_date").values_list("portfolio_date", flat=True).first()
    holdings = []
    if holding_date:
        holdings = list(
            holdings_queryset.filter(portfolio_date=holding_date)
            .order_by("-pct_of_nav", "security_name")[:250]
        )
    master_history = MFMasterData.objects.filter(scheme=scheme).order_by("as_of_date")
    aum_history = [
        {"date": item.aum_as_of, "aum_cr": item.aum_cr, "type": item.aum_type}
        for item in master_history if item.aum_source == SOURCE and item.aum_as_of and item.aum_cr is not None
    ]
    ter_history = [
        {
            "date": item.ter_as_of,
            "direct_pct": item.ter_direct_pct,
            "regular_pct": item.ter_regular_pct,
        }
        for item in master_history if item.ter_source == SOURCE and item.ter_as_of
    ]
    period_record = None
    for period_run in MFSourceRun.objects.filter(source=SOURCE, command="holdings_periods").order_by("-started_at")[:100]:
        if (period_run.metadata or {}).get("scope") == "scheme":
            period_record = next((row for row in (period_run.metadata or {}).get("schemes", []) if str(row.get("scheme_code")) == str(scheme.scheme_code)), None)
            if period_record:
                break
    nav_detail_run = MFSourceRun.objects.filter(
        source=SOURCE, command="latest_nav_detail", metadata__scheme_code=scheme.scheme_code
    ).order_by("-started_at").first()
    metrics_run = (
        MFSourceRun.objects.filter(
            source=SOURCE, command="metrics", metadata__scheme_code=scheme.scheme_code
        ).order_by("-started_at").first()
    )
    holdings_run = (
        MFSourceRun.objects.filter(
            source=SOURCE, command="holdings_changes", metadata__scheme_code=scheme.scheme_code
        ).order_by("-started_at").first()
    )
    return Response({
        "scheme": {
            "id": scheme.id,
            "scheme_code": scheme.scheme_code,
            "scheme_name": scheme.scheme_name,
            "amc": scheme.amc_name,
            "isin_growth": scheme.isin_growth,
            "isin_idcw": scheme.isin_dividend,
            "category": scheme.level_3_category,
            "plan": scheme.plan,
            "option": scheme.option,
        },
        "snapshot": IntelligenceSnapshotSerializer(snapshot).data if snapshot else None,
        "nav_history": [{"date": item.date, "nav": item.nav} for item in reversed(nav_rows)],
        "aum_history": aum_history,
        "ter_history": ter_history,
        "holdings": [
            {
                "name": item.security_name,
                "isin": item.isin,
                "ticker": item.ticker,
                "weight_pct": item.pct_of_nav,
                "quantity": item.quantity,
                "market_value": item.market_value,
                "sector": item.sector,
                "industry": item.industry,
                "instrument_type": item.instrument_type,
                "report_date": item.portfolio_date,
            }
            for item in holdings
        ],
        "holdings_report_date": holding_date,
        "holdings_periods": (period_record or {}).get("periods", []),
        "nav_detail": nav_detail_run.metadata if nav_detail_run else None,
        "provider_metrics": metrics_run.metadata if metrics_run else None,
        "holdings_changes": holdings_run.metadata.get("provider_changes") if holdings_run else None,
        "data_availability": {
            "nav": bool(nav_rows),
            "metrics": bool(metrics_run),
            "ter": bool(ter_history),
            "aum": bool(aum_history),
            "holdings": bool(holdings),
            "holdings_report_date": holding_date,
        },
    })


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def charliealpha_benchmarks(request):
    """Serve the latest provider-normalized series from relational observations."""
    slug = (request.query_params.get("slug") or "").strip()
    benchmarks = MFProviderBenchmark.objects.filter(provider="CHARLIEALPHA").order_by("category", "name")
    if slug:
        benchmarks = benchmarks.filter(slug=slug)
    results = []
    for benchmark in benchmarks[:100]:
        observations = MFProviderBenchmarkObservation.objects.filter(benchmark=benchmark)
        base_date = observations.order_by("-fetched_at").values_list("normalization_base_date", flat=True).first()
        if base_date is None:
            continue
        series = list(
            observations.filter(normalization_base_date=base_date)
            .order_by("observation_date")
            .values("observation_date", "normalized_value")
        )
        results.append({
            "slug": benchmark.slug,
            "name": benchmark.name,
            "category": benchmark.category,
            "normalization": {
                "is_normalized": True,
                "method": "REBASED_TO_100",
                "base_value": "100",
                "base_date": base_date,
                "warning": "Provider history is rebased to 100 at the start of the returned range; it is not an absolute official TRI series.",
            },
            "series": [
                {"date": item["observation_date"], "normalized_value": item["normalized_value"]}
                for item in series
            ],
            "fetched_at": benchmark.fetched_at,
        })
    return Response({"count": len(results), "results": results})

@api_view(["GET"])
@permission_classes([IsAuthenticated])
def charliealpha_category_metrics(request, slug: str):
    run = (
        MFSourceRun.objects.filter(
            source=SOURCE, command="category_metrics", metadata__category_slug=slug
        ).order_by("-started_at").first()
    )
    if not run:
        return Response({"detail": "Category metrics have not been ingested yet.", "slug": slug}, status=status.HTTP_404_NOT_FOUND)
    return Response({"slug": slug, "category_medians": run.metadata.get("category_medians"), "fetched_at": run.completed_at})


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def charliealpha_compare(request):
    """Read a previously ingested provider comparison; never call the provider from the UI."""
    codes = request.data.get("scheme_codes", [])
    if not isinstance(codes, list):
        return Response({"detail": "scheme_codes must be an array."}, status=status.HTTP_400_BAD_REQUEST)
    normalized = list(dict.fromkeys(str(code).strip() for code in codes if str(code).strip()))
    if not 2 <= len(normalized) <= 20:
        return Response({"detail": "Select between 2 and 20 funds."}, status=status.HTTP_400_BAD_REQUEST)

    overlap_only = bool(request.data.get("overlap_only", False))
    command = "overlap" if overlap_only else "compare"
    run = MFSourceRun.objects.filter(
        source=SOURCE,
        command=command,
        metadata__scheme_codes=normalized,
    ).order_by("-started_at").first()
    if run is None:
        return Response({
            "detail": "No stored comparison exists for this exact fund selection. "
                      "Run sync_charliealpha --domain " + command + " with these scheme codes first.",
            "scheme_codes": normalized,
        }, status=status.HTTP_404_NOT_FOUND)
    metadata = run.metadata or {}
    return Response({
        "run_id": run.id,
        "scheme_codes": normalized,
        "overlap_only": overlap_only,
        "result": metadata.get("provider_result"),
        "partial_result": metadata.get("provider_partial_result", False),
        "report_date_warning": metadata.get("report_date_warning"),
        "fetched_at": run.completed_at or run.started_at,
    })
