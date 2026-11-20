from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from watchlist.models import MutualFundResearchReport


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def latest_mutual_fund_research_report(request):
    """Return the latest imported, source-faithful multi-cap research report."""
    reports = MutualFundResearchReport.objects.exclude(report_key="indianapi-india")
    selected_id = request.query_params.get("id")
    if selected_id:
        try:
            report = reports.filter(pk=int(selected_id)).first()
        except (TypeError, ValueError):
            report = None
    else:
        report = reports.order_by("-report_date", "-imported_at").first()
    if report is None:
        return Response({
            "available": False,
            "message": "No research report has been imported yet. Import the supplied HTML report using the documented management command.",
        }, status=200)

    return Response({
        "available": True,
        "id": report.id,
        "report_key": report.report_key,
        "report_date": report.report_date.isoformat(),
        "report_cycle": report.report_cycle,
        "source_file": report.source_file,
        "imported_at": report.imported_at.isoformat(),
        "payload": report.payload,
    })



@api_view(["GET"])
@permission_classes([IsAuthenticated])
def mutual_fund_research_universes(request):
    """List imported research snapshots that can be selected as dashboard universes."""
    reports = (
        MutualFundResearchReport.objects
        .exclude(report_key="indianapi-india")
        .order_by("-report_date", "-imported_at")
    )
    results = []
    for report in reports:
        payload = report.payload if isinstance(report.payload, dict) else {}
        raw = payload.get("RAW") if isinstance(payload.get("RAW"), dict) else {}
        meta = raw.get("meta") if isinstance(raw.get("meta"), dict) else {}
        funds = raw.get("funds") if isinstance(raw.get("funds"), list) else []
        results.append({
            "id": report.id,
            "report_key": report.report_key,
            "report_date": report.report_date.isoformat(),
            "report_cycle": report.report_cycle,
            "source_file": report.source_file,
            "category": str(meta.get("category") or report.report_key),
            "fund_count": len(funds),
            "imported_at": report.imported_at.isoformat(),
        })
    return Response({
        "available": bool(results),
        "count": len(results),
        "results": results,
        "message": "" if results else "No imported mutual-fund research universes are available.",
    })
