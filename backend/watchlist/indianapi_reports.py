"""Read-only API for the optional IndianAPI snapshot and keyless AMFI fallback."""
from django.conf import settings
from django.db.models import OuterRef, Subquery
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from mutual_funds.models import AMFIMasterNAV, AMFIMasterScheme
from watchlist.models import MutualFundResearchReport
from watchlist.services.indianapi import normalize_mutual_fund_payload


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def india_mutual_fund_feed(request):
    """Return cached IndianAPI data when present, otherwise the public AMFI master."""
    configured = bool(str(getattr(settings, "INDIANAPI_API_KEY", "") or "").strip())
    report = (
        MutualFundResearchReport.objects
        .filter(report_key="indianapi-india")
        .order_by("-report_date", "-imported_at")
        .first()
    )
    if report:
        payload = report.payload or {}
        funds = payload.get("funds")
        if not isinstance(funds, list):
            funds = normalize_mutual_fund_payload(payload.get("raw", {}))
        return Response({
            "available": bool(funds),
            "provider_configured": configured,
            "source": "IndianAPI",
            "source_label": "IndianAPI · cached mutual-fund snapshot",
            "report_date": report.report_date,
            "imported_at": report.imported_at,
            "count": len(funds),
            "total_count": len(funds),
            "message": (
                "Showing the last stored IndianAPI snapshot."
                if configured else
                "Showing the last stored IndianAPI snapshot. IndianAPI refresh is disabled until an API key is configured."
            ),
            "funds": funds,
        })

    search = str(request.query_params.get("search", "")).strip()
    try:
        limit = min(max(int(request.query_params.get("limit", 500)), 1), 1000)
    except (TypeError, ValueError):
        limit = 500

    latest_nav = AMFIMasterNAV.objects.filter(
        scheme_id=OuterRef("pk")
    ).order_by("-date")
    schemes = AMFIMasterScheme.objects.filter(is_active=True)
    if search:
        schemes = schemes.filter(scheme_name__icontains=search)
    total_count = schemes.count()
    schemes = schemes.annotate(
        latest_nav_value=Subquery(latest_nav.values("nav")[:1]),
        latest_nav_date=Subquery(latest_nav.values("date")[:1]),
    ).order_by("scheme_name")[:limit]

    funds = [{
        "fund_name": scheme.scheme_name,
        "asset_class": "Mutual Fund",
        "category": None,
        "latest_nav": float(scheme.latest_nav_value) if scheme.latest_nav_value is not None else None,
        "nav_date": scheme.latest_nav_date,
        "percentage_change": None,
        "asset_size": None,
        "returns": {},
        "star_rating": None,
        "scheme_code": scheme.scheme_code,
        "isin_growth": scheme.isin_growth,
        "isin_dividend": scheme.isin_dividend,
    } for scheme in schemes]
    latest_date = AMFIMasterNAV.objects.order_by("-date").values_list("date", flat=True).first()
    return Response({
        "available": bool(funds),
        "provider_configured": configured,
        "source": "AMFI",
        "source_label": "AMFI · public scheme master and NAV",
        "report_date": latest_date,
        "imported_at": None,
        "count": len(funds),
        "total_count": total_count,
        "message": (
            "Showing the keyless AMFI scheme/NAV master. Configure INDIANAPI_API_KEY and run "
            "sync_indianapi_mutual_funds to add optional category, AUM, rating and return enrichment."
            if funds else
            "No AMFI master data is stored yet. Run refresh_amfi_master; this does not require an API key."
        ),
        "funds": funds,
    })
