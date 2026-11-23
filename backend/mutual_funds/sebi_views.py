import json
from pathlib import Path
from datetime import timedelta

from django.utils import timezone

from django.http import FileResponse, Http404
from django.conf import settings
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .models import AMFIMasterNAV, AMFIMasterScheme, AMFISchemeAAUM
from .services.sebi_mf_analytics import (
    analytics_data_dir,
    analytics_export_dir,
    compute_fund_performance_metrics,
    load_cached_portfolio_holdings,
    load_latest_sebi_summary,
)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def sebi_universe_summary(request):
    """Read the latest locally generated SEBI universe summary; never fetches providers."""
    summary = load_latest_sebi_summary()
    if summary is None:
        return Response({
            "available": False,
            "message": (
                "No SEBI universe analytics snapshot is available. From backend/, run "
                "'python manage.py build_sebi_mf_analytics --plans both'. "
                "Run ingest_sebi_mf_universe first only when the SEBI category universe is empty."
            ),
            "download_url": None,
        })
    return Response(summary)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def sebi_universe_rankings(request):
    """Read cached fund rankings; pagination/filters never call MFapi or IndianAPI."""
    path = analytics_data_dir() / "latest_rankings.json"
    if not path.is_file():
        return Response({
            "available": False,
            "message": (
                "No rankings snapshot is available. From backend/, run "
                "'python manage.py build_sebi_mf_analytics --plans both'."
            ),
            "count": 0,
            "results": [],
        })

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        rows = payload.get("rankings", [])
    except (OSError, json.JSONDecodeError):
        return Response({
            "available": False,
            "message": "The local rankings snapshot could not be read.",
            "count": 0,
            "results": [],
        }, status=503)

    category = (request.query_params.get("category") or "").strip()
    plan = (request.query_params.get("plan") or "").strip()
    quartile = (request.query_params.get("quartile") or "").strip().upper()
    if category:
        rows = [row for row in rows if row.get("Level_3_Category") == category]
    if plan:
        rows = [row for row in rows if row.get("Plan") == plan]
    if quartile:
        rows = [row for row in rows if row.get("Quartile") == quartile]

    try:
        page = max(1, int(request.query_params.get("page", "1")))
        page_size = min(500, max(1, int(request.query_params.get("page_size", "100"))))
    except ValueError:
        return Response({"detail": "page and page_size must be integers."}, status=400)

    count = len(rows)
    start = (page - 1) * page_size
    return Response({
        "available": True,
        "count": count,
        "page": page,
        "page_size": page_size,
        "results": rows[start:start + page_size],
    })


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def sebi_fund_nav_history(request, scheme_code):
    """Return locally persisted positive NAV observations and calculated metrics.

    This endpoint is read-only and never contacts MFapi, IndianAPI, or an AMC.
    The most recent 3,000 observations are returned to keep chart payloads bounded.
    """
    scheme = AMFIMasterScheme.objects.filter(
        scheme_code=str(scheme_code),
        is_active=True,
        sebi_universe_active=True,
        is_growth_plan=True,
    ).values(
        "scheme_code",
        "scheme_name",
        "amc_name",
        "plan",
        "option",
        "level_1_branch",
        "level_2_group",
        "level_3_category",
    ).first()
    if scheme is None:
        raise Http404("SEBI Growth scheme was not found.")

    recent_rows = AMFIMasterNAV.objects.filter(
        scheme__scheme_code=str(scheme_code),
        nav__gt=0,
    ).order_by("-date", "-id")[:6000]
    # Multiple providers can store a NAV for the same date. Keep one positive
    # observation per date so duplicate source rows do not distort returns.
    by_date = {}
    for row in recent_rows:
        by_date.setdefault(row.date, row)
    observations = sorted(by_date.values(), key=lambda row: row.date)[-3000:]
    history = [
        {"date": row.date.isoformat(), "nav": float(row.nav)}
        for row in observations
    ]
    series = [(row.date, float(row.nav)) for row in observations]
    metrics = compute_fund_performance_metrics(series) if series else {}
    portfolio = next(
        (
            item for item in load_cached_portfolio_holdings()
            if str(item.get("scheme_code")) == str(scheme_code)
        ),
        None,
    )

    return Response({
        "available": bool(history),
        "scheme": scheme,
        "history": history,
        "metrics": metrics,
        "portfolio": portfolio,
        "history_count": len(history),
        "message": None if history else (
            "No locally persisted NAV history is available for this scheme. "
            "Run the SEBI NAV analytics build after the NAV cache is populated."
        ),
    })


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def sebi_category_top_aaum(request):
    """Return top 12 scheme-wise AAUM values from the latest imported AMFI quarter."""
    category = (request.query_params.get("category") or "").strip()
    plan = (request.query_params.get("plan") or "").strip()
    if not category or plan not in {"Direct Plan", "Regular Plan"}:
        return Response({"detail": "category and a valid plan are required."}, status=400)

    latest_quarter = AMFISchemeAAUM.objects.filter(
        scheme__is_active=True,
        scheme__sebi_universe_active=True,
        scheme__is_growth_plan=True,
        scheme__level_3_category=category,
        scheme__plan=plan,
    ).order_by("-quarter_start").values_list("quarter_start", flat=True).first()
    if latest_quarter is None:
        return Response({
            "available": False, "category": category, "plan": plan, "rows": [],
            "message": "No AMFI scheme-wise AAUM has been imported yet.",
        })

    snapshots = AMFISchemeAAUM.objects.filter(
        quarter_start=latest_quarter,
        scheme__is_active=True,
        scheme__sebi_universe_active=True,
        scheme__is_growth_plan=True,
        scheme__level_3_category=category,
        scheme__plan=plan,
    ).select_related("scheme").order_by("-average_aum_cr", "scheme__scheme_name")[:12]

    rows = [{
        "scheme_code": item.scheme.scheme_code,
        "scheme_name": item.scheme.scheme_name,
        "amc_name": item.scheme.amc_name,
        "plan": item.scheme.plan,
        "average_aum_cr": float(item.average_aum_cr),
    } for item in snapshots]
    quarter_label = snapshots[0].quarter_label if snapshots else ""
    return Response({
        "available": bool(rows), "category": category, "plan": plan,
        "quarter_start": latest_quarter.isoformat(), "quarter_label": quarter_label,
        "rows": rows,
        "message": None if rows else "No matched scheme AAUM records exist for this category and plan.",
    })


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def sebi_category_return_distribution(request):
    """Count category funds in 5-point trailing-return bands using cached NAV only."""
    category = (request.query_params.get("category") or "").strip()
    plan = (request.query_params.get("plan") or "").strip()
    if not category or plan not in {"Direct Plan", "Regular Plan"}:
        return Response({"detail": "category and a valid plan are required."}, status=400)

    today = timezone.localdate()
    periods = {"3M": today - timedelta(days=91), "6M": today - timedelta(days=182), "1Y": today - timedelta(days=365)}
    schemes = list(AMFIMasterScheme.objects.filter(
        is_active=True, sebi_universe_active=True, is_growth_plan=True,
        level_3_category=category, plan=plan,
    ).values_list("id", flat=True))
    if not schemes:
        return Response({"available": False, "category": category, "plan": plan, "bins": [], "funds_counted": {key: 0 for key in periods}})

    nav_rows = AMFIMasterNAV.objects.filter(
        scheme_id__in=schemes, date__gte=min(periods.values()) - timedelta(days=10),
        date__lte=today, nav__gt=0,
    ).order_by("scheme_id", "date", "-id").values_list("scheme_id", "date", "nav")
    by_scheme = {}
    for scheme_id, nav_date, nav in nav_rows:
        by_scheme.setdefault(scheme_id, []).append((nav_date, float(nav)))

    returns = {key: [] for key in periods}
    for observations in by_scheme.values():
        if len(observations) < 2:
            continue
        latest_date, latest_nav = observations[-1]
        if latest_nav <= 0:
            continue
        for period, target in periods.items():
            baseline_pair = next(((nav_date, nav) for nav_date, nav in reversed(observations) if nav_date <= target), None)
            if baseline_pair is None:
                continue
            baseline_date, baseline_nav = baseline_pair
            if baseline_nav <= 0 or baseline_date < target - timedelta(days=10):
                continue
            returns[period].append((latest_nav / baseline_nav - 1) * 100)

    values = [value for group in returns.values() for value in group]
    if not values:
        return Response({"available": False, "category": category, "plan": plan, "bins": [], "funds_counted": {key: 0 for key in periods}, "message": "Insufficient locally cached NAV history for this category."})

    lower = int(min(values) // 5) * 5
    upper = int(max(values) // 5) * 5
    starts = range(lower, upper + 1, 5)
    bins = []
    for start in starts:
        bins.append({
            "label": f"{start} to {start + 5}", "start": start,
            **{period: sum(start <= value < start + 5 for value in group) for period, group in returns.items()},
        })
    return Response({
        "available": True, "category": category, "plan": plan, "bin_width_pct": 5,
        "funds_counted": {key: len(group) for key, group in returns.items()},
        "bins": bins, "as_of_date": today.isoformat(),
    })


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def download_sebi_analytics_workbook(request):
    """Serve the fixed generated workbook path; clients cannot supply filesystem paths."""
    path = analytics_export_dir() / "SEBI_Universe_MF_Analytics.xlsx"
    if not path.is_file():
        raise Http404("SEBI analytics workbook has not been generated yet.")
    return FileResponse(
        path.open("rb"),
        as_attachment=True,
        filename="SEBI_Universe_MF_Analytics.xlsx",
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def download_sebi_category_risk_chart(request):
    path = analytics_export_dir() / "SEBI_Category_Return_vs_Risk.png"
    if not path.is_file():
        raise Http404("SEBI category risk chart has not been generated yet.")
    return FileResponse(path.open("rb"), content_type="image/png")


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def download_sebi_q1_chart(request):
    path = analytics_export_dir() / "SEBI_Top_Q1_vs_Category_Average.png"
    if not path.is_file():
        raise Http404("SEBI Q1 comparison chart has not been generated yet.")
    return FileResponse(path.open("rb"), content_type="image/png")
