import json
from pathlib import Path

from django.http import FileResponse, Http404
from django.conf import settings
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .models import AMFIMasterNAV, AMFIMasterScheme
from .services.sebi_mf_analytics import (
    analytics_data_dir,
    analytics_export_dir,
    compute_fund_performance_metrics,
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
                "No SEBI universe analytics snapshot is available. Run "
                "ingest_sebi_mf_universe followed by build_sebi_mf_analytics."
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
            "message": "No rankings snapshot is available. Run build_sebi_mf_analytics.",
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

    return Response({
        "available": bool(history),
        "scheme": scheme,
        "history": history,
        "metrics": metrics,
        "history_count": len(history),
        "message": None if history else (
            "No locally persisted NAV history is available for this scheme. "
            "Run the SEBI NAV analytics build after the NAV cache is populated."
        ),
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
