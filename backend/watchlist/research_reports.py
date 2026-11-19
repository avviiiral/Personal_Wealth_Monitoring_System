from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from watchlist.models import MutualFundResearchReport


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def latest_mutual_fund_research_report(request):
    """Return the latest imported, source-faithful multi-cap research report."""
    report = (
        MutualFundResearchReport.objects
        .exclude(report_key="indianapi-india")
        .order_by("-report_date", "-imported_at")
        .first()
    )
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
