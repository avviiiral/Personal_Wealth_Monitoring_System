"""Authenticated, database-backed APIs for the Mutual Fund Intelligence page."""
import re

from django.db.models import F, OuterRef, Q, Subquery
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
from mutual_funds.services.charliealpha import CharlieAlphaService, NAV_SOURCE, SOURCE, decimal_value, parse_date


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


_SNAPSHOT_GROUP_FIELDS = {
    "nav": ("nav", "nav_as_of", "nav_source"),
    "performance_and_risk": (
        "return_1y_pct", "return_3y_pct", "return_5y_pct", "return_10y_pct",
        "since_inception_cagr_pct", "rolling_3y_cagr_mean_pct",
        "rolling_3y_cagr_min_pct", "rolling_3y_cagr_max_pct",
        "rolling_3y_cagr_observations", "std_dev_pct", "sharpe", "sortino",
        "beta", "alpha_pct", "information_ratio", "max_drawdown_pct",
        "risk_free_rate_used_pct", "ratio_window_years",
        "upside_capture_pct", "downside_capture_pct", "tracking_error_pct",
    ),
    "ter": ("ter_direct_pct", "ter_regular_pct", "ter_as_of", "ter_source"),
    "aum": ("aum_cr", "aum_type", "aum_as_of", "aum_source"),
    "holdings": (
        "top10_holdings", "sector_allocation", "equity_pct", "debt_pct",
        "cash_pct", "others_pct", "holdings_as_of", "holdings_source",
    ),
}


def _serialize_current_snapshots(snapshots):
    """Combine each provider domain's latest non-null values without changing dated history rows."""
    snapshot_rows = list(snapshots)
    if not snapshot_rows:
        return []
    serialized = {
        row.scheme_id: dict(IntelligenceSnapshotSerializer(row).data)
        for row in snapshot_rows
    }
    found: set[tuple[int, str]] = set()

    for domain, fields in _SNAPSHOT_GROUP_FIELDS.items():
        lookup = {f"field_sources__{domain}__source": SOURCE}
        rows = MFMasterData.objects.filter(
            scheme_id__in=serialized.keys(),
            **lookup,
        ).order_by("scheme_id", "-as_of_date", "-id")
        for row in rows:
            target = serialized[row.scheme_id]
            for field in fields:
                marker = (row.scheme_id, field)
                if marker in found:
                    continue
                value = getattr(row, field)
                if value is None or value == [] or value == {} or value == "":
                    continue
                target[field] = value
                found.add(marker)
            field_sources = dict(target.get("field_sources") or {})
            source_info = (row.field_sources or {}).get(domain)
            if source_info:
                field_sources[domain] = source_info
                target["field_sources"] = field_sources
                target[domain + "_snapshot_as_of"] = row.as_of_date

    return [serialized[row.scheme_id] for row in snapshot_rows]


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
    results = _serialize_current_snapshots(page)

    latest = _latest_snapshots()
    latest_date = latest.order_by("-as_of_date").values_list("as_of_date", flat=True).first()
    nav_coverage = AMFIMasterNAV.objects.filter(source=NAV_SOURCE).values("scheme_id").distinct().count()
    holdings_coverage = MFMasterHolding.objects.filter(source=SOURCE).values("scheme_id").distinct().count()
    metric_coverage = (
        MFSourceRun.objects.filter(source=SOURCE, command="metrics", status__in=("SUCCESS", "PARTIAL"))
        .values("metadata__scheme_code").distinct().count()
    )
    latest_scheme_ids = latest.values("scheme_id").distinct()
    linked_schemes = AMFIMasterScheme.objects.filter(id__in=Subquery(latest_scheme_ids))
    summary = {
        "active_amfi_schemes": AMFIMasterScheme.objects.filter(is_active=True).count(),
        "schemes_with_masterdata": linked_schemes.count(),
        "amc_coverage": linked_schemes.exclude(amc_name="").values("amc_name").distinct().count(),
        "category_coverage": linked_schemes.exclude(level_3_category="").values("level_3_category").distinct().count(),
        "nav_coverage": nav_coverage,
        "metrics_coverage": metric_coverage,
        "holdings_coverage": holdings_coverage,
        "latest_snapshot_date": latest_date,
        "provider": SOURCE,
    }
    filters = {
        "amcs": list(linked_schemes.exclude(amc_name="").values_list("amc_name", flat=True).distinct().order_by("amc_name")),
        "categories": list(linked_schemes.exclude(level_3_category="").values_list("level_3_category", flat=True).distinct().order_by("level_3_category")),
        "plans": list(linked_schemes.exclude(plan="").values_list("plan", flat=True).distinct().order_by("plan")),
        "options": list(linked_schemes.exclude(option="").values_list("option", flat=True).distinct().order_by("option")),
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
    if scheme is None and len(scheme_code) == 12:
        scheme = AMFIMasterScheme.objects.filter(
            Q(isin_growth__iexact=scheme_code) | Q(isin_dividend__iexact=scheme_code),
            is_active=True,
        ).first()
    if scheme is None:
        raise Http404("Mutual-fund scheme or ISIN was not found in the AMFI master.")

    snapshot_row = _latest_snapshots().filter(scheme=scheme).first()
    snapshot_payload = _serialize_current_snapshots([snapshot_row])[0] if snapshot_row else None
    try:
        nav_limit = int(request.query_params.get("nav_limit", 1000))
    except (TypeError, ValueError):
        return Response({"detail": "nav_limit must be an integer between 1 and 5000."}, status=status.HTTP_400_BAD_REQUEST)
    if nav_limit < 1 or nav_limit > 5000:
        return Response({"detail": "nav_limit must be between 1 and 5000."}, status=status.HTTP_400_BAD_REQUEST)

    nav_queryset = AMFIMasterNAV.objects.filter(scheme=scheme, source=NAV_SOURCE)
    raw_from_date = request.query_params.get("from_date")
    raw_to_date = request.query_params.get("to_date")
    from_date = parse_date(raw_from_date) if raw_from_date else None
    to_date = parse_date(raw_to_date) if raw_to_date else None
    if raw_from_date and from_date is None:
        return Response({"detail": "from_date must use YYYY-MM-DD."}, status=status.HTTP_400_BAD_REQUEST)
    if raw_to_date and to_date is None:
        return Response({"detail": "to_date must use YYYY-MM-DD."}, status=status.HTTP_400_BAD_REQUEST)
    if from_date and to_date and from_date > to_date:
        return Response({"detail": "from_date cannot be later than to_date."}, status=status.HTTP_400_BAD_REQUEST)
    if from_date:
        nav_queryset = nav_queryset.filter(date__gte=from_date)
    if to_date:
        nav_queryset = nav_queryset.filter(date__lte=to_date)
    nav_rows = list(nav_queryset.order_by("-date")[:nav_limit])
    holdings_queryset = MFMasterHolding.objects.filter(scheme=scheme, source=SOURCE)
    raw_holding_date = (request.query_params.get("holdings_as_of") or "").strip()
    requested_holding_date = parse_date(raw_holding_date) if raw_holding_date else None
    if raw_holding_date and requested_holding_date is None:
        return Response({"detail": "holdings_as_of must use YYYY-MM-DD."}, status=status.HTTP_400_BAD_REQUEST)
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
        "snapshot": snapshot_payload,
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
                "rating": item.rating,
                "rating_agency": item.rating_agency,
                "maturity_date": item.maturity_date,
                "ytm_pct": item.ytm_pct,
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
def charliealpha_category_catalog(request):
    """Return the stored provider category tree and canonical category slugs."""
    run = MFSourceRun.objects.filter(
        source=SOURCE, command="categories"
    ).order_by("-started_at").first()
    if run is None:
        return Response(
            {"detail": "CharlieAlpha categories have not been ingested yet."},
            status=status.HTTP_404_NOT_FOUND,
        )
    metadata = run.metadata or {}
    return Response({
        "count": metadata.get("category_count", 0),
        "asset_classes": metadata.get("asset_classes", []),
        "results": metadata.get("category_rows", []),
        "category_slugs": metadata.get("category_slugs", []),
        "fetched_at": run.completed_at or run.started_at,
    })


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def charliealpha_category_metrics(request, slug: str):
    horizon = (request.query_params.get("horizon") or "").strip().lower()
    if horizon and horizon not in {"1y", "3y", "5y", "10y"}:
        return Response({"detail": "horizon must be one of 1y, 3y, 5y or 10y."}, status=status.HTTP_400_BAD_REQUEST)
    runs = list(MFSourceRun.objects.filter(
        source=SOURCE,
        command="category_metrics",
        metadata__category_slug=slug,
        status__in=(MFSourceRun.Status.SUCCESS, MFSourceRun.Status.PARTIAL),
    ).order_by("-started_at")[:100])
    run = next((
        candidate for candidate in runs
        if (candidate.metadata or {}).get("horizon") == horizon
    ), None) if horizon else (runs[0] if runs else None)
    # A no-horizon ingestion stores all available horizons in one response.
    # Reuse it when a caller asks for one horizon but no horizon-filtered run exists.
    if run is None and horizon:
        run = next((
            candidate for candidate in runs
            if (candidate.metadata or {}).get("horizon") is None
        ), None)
    if not run:
        return Response({"detail": "Category metrics have not been ingested for this category and horizon.", "slug": slug, "horizon": horizon or None}, status=status.HTTP_404_NOT_FOUND)
    metadata = run.metadata or {}
    return Response({
        "slug": slug,
        "horizon": metadata.get("horizon"),
        "category_medians": metadata.get("category_medians"),
        "metrics_count": metadata.get("metrics_count", 0),
        "fetched_at": run.completed_at or run.started_at,
        "status": run.status,
    })


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def charliealpha_compare(request):
    """Read a previously ingested provider comparison; never call the provider from the UI."""
    if not isinstance(request.data, dict):
        return Response({"detail": "The request body must be a JSON object."}, status=status.HTTP_400_BAD_REQUEST)
    codes = request.data.get("scheme_codes", [])
    if not isinstance(codes, list):
        return Response({"detail": "scheme_codes must be an array."}, status=status.HTTP_400_BAD_REQUEST)
    normalized = list(dict.fromkeys(str(code).strip().upper() for code in codes if str(code).strip()))
    if not 2 <= len(normalized) <= 20:
        return Response({"detail": "Select between 2 and 20 funds."}, status=status.HTTP_400_BAD_REQUEST)
    isin_pattern = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")
    invalid = [code for code in normalized if not (code.isdigit() and len(code) == 6) and not isin_pattern.fullmatch(code)]
    if invalid:
        return Response({"detail": "Each scheme identifier must be a six-digit AMFI code or a 12-character ISIN.", "invalid_identifiers": invalid}, status=status.HTTP_400_BAD_REQUEST)

    overlap_only = request.data.get("overlap_only", False)
    if not isinstance(overlap_only, bool):
        return Response({"detail": "overlap_only must be a boolean."}, status=status.HTTP_400_BAD_REQUEST)
    command = "overlap" if overlap_only else "compare"
    run = next((
        candidate for candidate in MFSourceRun.objects.filter(
            source=SOURCE, command=command,
            status__in=(MFSourceRun.Status.SUCCESS, MFSourceRun.Status.PARTIAL),
        ).order_by("-started_at")[:500]
        if (candidate.metadata or {}).get("scheme_codes") == normalized
        and "provider_result" in (candidate.metadata or {})
    ), None)
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


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def charliealpha_holdings_periods(request, scheme_code: str | None = None):
    """Read global or scheme-level disclosure periods persisted during ingestion."""
    if scheme_code:
        requested_identifier = str(scheme_code).strip()
        resolved_scheme = AMFIMasterScheme.objects.filter(
            scheme_code=requested_identifier
        ).first()
        if resolved_scheme is None and len(requested_identifier) == 12:
            resolved_scheme = AMFIMasterScheme.objects.filter(
                Q(isin_growth__iexact=requested_identifier)
                | Q(isin_dividend__iexact=requested_identifier)
            ).first()
        canonical_code = resolved_scheme.scheme_code if resolved_scheme else requested_identifier
        runs = MFSourceRun.objects.filter(
            source=SOURCE, command="holdings_periods"
        ).order_by("-started_at")[:100]
        for run in runs:
            metadata = run.metadata or {}
            if metadata.get("scope") != "scheme":
                continue
            match = next((
                row for row in metadata.get("schemes", [])
                if str(row.get("scheme_code")) == canonical_code
                or str(row.get("provider_identifier", "")).casefold() == requested_identifier.casefold()
            ), None)
            if match is not None:
                return Response(match)
        return Response(
            {
                "detail": "Holdings disclosure periods have not been ingested for this scheme.",
                "scheme_code": scheme_code,
            },
            status=status.HTTP_404_NOT_FOUND,
        )

    run = MFSourceRun.objects.filter(
        source=SOURCE,
        command="holdings_periods",
        metadata__scope="global",
    ).order_by("-started_at").first()
    if run is None:
        return Response(
            {"detail": "Global holdings disclosure periods have not been ingested."},
            status=status.HTTP_404_NOT_FOUND,
        )
    return Response(run.metadata or {})


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def charliealpha_security_exposure(request, identifier: str):
    """Read the most recently ingested provider exposure response for a security."""
    runs = MFSourceRun.objects.filter(
        source=SOURCE,
        command="fund_exposure",
    ).order_by("-started_at")[:500]
    selected = None
    raw_min_pct = (request.query_params.get("min_pct") or "").strip()
    requested_min_pct = decimal_value(raw_min_pct) if raw_min_pct else None
    if raw_min_pct and (requested_min_pct is None or requested_min_pct < 0):
        return Response({"detail": "min_pct must be a non-negative number."}, status=status.HTTP_400_BAD_REQUEST)
    for run in runs:
        metadata = run.metadata or {}
        if run.status not in (MFSourceRun.Status.SUCCESS, MFSourceRun.Status.PARTIAL):
            continue
        if "provider_result" not in metadata:
            continue
        if str(metadata.get("identifier", "")).casefold() != identifier.casefold():
            continue
        stored_min_pct = decimal_value(metadata.get("min_pct")) if metadata.get("min_pct") not in (None, "") else None
        if raw_min_pct and stored_min_pct != requested_min_pct:
            continue
        selected = run
        break
    if selected is None:
        return Response(
            {
                "detail": "No stored exposure result for this security. "
                          "Run the exposure synchronization command first."
            },
            status=status.HTTP_404_NOT_FOUND,
        )
    metadata = selected.metadata or {}
    return Response({
        "identifier": identifier,
        "min_pct": metadata.get("min_pct"),
        "result": metadata.get("provider_result"),
        "fetched_at": selected.completed_at or selected.started_at,
    })
