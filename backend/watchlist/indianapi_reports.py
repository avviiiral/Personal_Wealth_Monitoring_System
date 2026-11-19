"""Cached India mutual-fund feed, explicit refresh and quota status endpoints."""
from datetime import timedelta

from django.conf import settings
from django.db.models import OuterRef, Subquery
from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from mutual_funds.models import AMFIMasterNAV, AMFIMasterScheme
from users.permissions import CanWriteFinancialData
from watchlist.models import IndianAPIRequestUsage, MutualFundResearchReport
from watchlist.services.indianapi import (
    MAX_MONTHLY_REQUEST_BUDGET,
    IndianAPIClient,
    IndianAPIError,
    IndianAPIQuotaExceeded,
    IndianAPIRefreshInProgress,
    IndianAPIRateLimitError,
    normalize_mutual_fund_payload,
)


CACHE_TTL = timedelta(hours=24)


def _latest_indianapi_report():
    return (
        MutualFundResearchReport.objects
        .filter(report_key="indianapi-india")
        .order_by("-report_date", "-imported_at")
        .first()
    )


def _overlay_amfi_nav(funds):
    """Overlay AMFI identity and NAV when an exact scheme-name mapping exists."""
    names = {
        str(row.get("fund_name") or "").strip()
        for row in funds
        if isinstance(row, dict) and str(row.get("fund_name") or "").strip()
    }
    if not names:
        return funds

    latest_nav = AMFIMasterNAV.objects.filter(
        scheme_id=OuterRef("pk")
    ).order_by("-date", "-id")
    schemes = AMFIMasterScheme.objects.filter(
        is_active=True,
        scheme_name__in=names,
    ).annotate(
        latest_nav_value=Subquery(latest_nav.values("nav")[:1]),
        latest_nav_date=Subquery(latest_nav.values("date")[:1]),
    )
    by_name = {scheme.scheme_name.casefold(): scheme for scheme in schemes}

    for fund in funds:
        if not isinstance(fund, dict):
            continue
        scheme = by_name.get(str(fund.get("fund_name") or "").strip().casefold())
        if scheme is None:
            # Do not label a provider-supplied NAV as AMFI sourced.
            fund["latest_nav"] = None
            fund["nav_date"] = None
            continue
        fund["scheme_code"] = scheme.scheme_code
        fund["isin_growth"] = scheme.isin_growth
        fund["isin_dividend"] = scheme.isin_dividend
        fund["latest_nav"] = (
            float(scheme.latest_nav_value)
            if scheme.latest_nav_value is not None else None
        )
        fund["nav_date"] = scheme.latest_nav_date
    return funds


def _usage_payload():
    month = timezone.localdate().strftime("%Y-%m")
    configured_budget = int(getattr(settings, "INDIANAPI_MONTHLY_REQUEST_BUDGET", MAX_MONTHLY_REQUEST_BUDGET))
    budget = min(MAX_MONTHLY_REQUEST_BUDGET, max(1, configured_budget))
    usage = IndianAPIRequestUsage.objects.filter(month=month).first()
    report = _latest_indianapi_report()
    used = usage.request_count if usage else 0
    return {
        "month": month,
        "monthly_budget": budget,
        "requests_used": used,
        "requests_remaining": max(0, budget - used),
        "last_request_timestamp": usage.last_requested_at if usage else None,
        "last_refresh_status": usage.refresh_status if usage else "NEVER",
        "last_refresh_at": (
            (usage.last_refresh_completed_at or usage.last_refresh_started_at)
            if usage else None
        ),
        "last_refresh_error": usage.last_refresh_error if usage else "",
        "budget_exhausted": used >= budget,
        "provider_configured": bool(str(getattr(settings, "INDIANAPI_API_KEY", "") or "").strip()),
        "data_last_updated_at": report.imported_at if report else None,
        "data_report_date": report.report_date if report else None,
    }


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def indianapi_usage_status(request):
    """Return PWMS-side accounting; IndianAPI provider dashboard remains authoritative."""
    return Response(_usage_payload())


@api_view(["POST"])
@permission_classes([IsAuthenticated, CanWriteFinancialData])
def refresh_india_mutual_fund_feed(request):
    """Explicit cache-first refresh using at most one bulk provider request."""
    client = IndianAPIClient()
    report = _latest_indianapi_report()
    now = timezone.now()

    if not client.is_configured:
        return Response({
            "refreshed": False,
            "cached": bool(report),
            "message": "IndianAPI is not configured. AMFI and previously cached data remain available.",
            "usage": _usage_payload(),
        })

    if report and report.imported_at and now - report.imported_at < CACHE_TTL:
        return Response({
            "refreshed": False,
            "cached": True,
            "message": "A valid IndianAPI snapshot is already cached. No provider request was made.",
            "usage": _usage_payload(),
        })

    try:
        raw_payload = client.fetch_mutual_funds()
        funds = normalize_mutual_fund_payload(raw_payload)
        if not funds:
            message = "IndianAPI returned no supported mutual-fund records; the previous snapshot was preserved."
            client.mark_refresh_status("FAILED", message)
            return Response({"refreshed": False, "message": message, "usage": _usage_payload()}, status=502)

        retrieved_at = timezone.now()
        saved, created = MutualFundResearchReport.objects.update_or_create(
            report_key="indianapi-india",
            report_date=timezone.localdate(retrieved_at),
            defaults={
                "report_cycle": "IndianAPI India mutual funds",
                "source_file": "IndianAPI /mutual_funds",
                "payload": {
                    "provider": "IndianAPI",
                    "retrieved_at": retrieved_at.isoformat(),
                    "funds": funds,
                    "raw": raw_payload,
                },
            },
        )
        client.mark_refresh_status("SUCCESS")
        return Response({
            "refreshed": True,
            "cached": False,
            "created": created,
            "message": f"Updated the cached India mutual-fund universe with {len(funds)} records.",
            "data_last_updated_at": saved.imported_at,
            "usage": _usage_payload(),
        })
    except IndianAPIRefreshInProgress as exc:
        return Response({"refreshed": False, "message": str(exc), "usage": _usage_payload()}, status=409)
    except IndianAPIQuotaExceeded as exc:
        return Response({"refreshed": False, "message": str(exc), "usage": _usage_payload()}, status=429)
    except IndianAPIRateLimitError as exc:
        return Response({"refreshed": False, "message": str(exc), "usage": _usage_payload()}, status=503)
    except IndianAPIError as exc:
        return Response({"refreshed": False, "message": str(exc), "usage": _usage_payload()}, status=502)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def india_mutual_fund_feed(request):
    """Return cached IndianAPI snapshot or keyless AMFI scheme/NAV master."""
    configured = bool(str(getattr(settings, "INDIANAPI_API_KEY", "") or "").strip())
    report = _latest_indianapi_report()
    if report:
        payload = report.payload or {}
        funds = payload.get("funds")
        if not isinstance(funds, list):
            funds = normalize_mutual_fund_payload(payload.get("raw", {}))
        funds = _overlay_amfi_nav(funds)
        return Response({
            "available": bool(funds),
            "provider_configured": configured,
            "source": "IndianAPI",
            "source_label": "IndianAPI enrichment · AMFI identity and NAV where exact scheme mapping exists",
            "report_date": report.report_date,
            "imported_at": report.imported_at,
            "count": len(funds),
            "total_count": len(funds),
            "message": (
                "Showing the last stored IndianAPI enrichment snapshot with AMFI NAV values where exact scheme mapping exists."
                if configured else
                "Showing the last stored IndianAPI snapshot. Provider refresh is disabled until an API key is configured."
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
            "Showing the keyless AMFI scheme/NAV master. Optional IndianAPI enrichment is available through an explicit, quota-protected refresh."
            if funds else
            "No AMFI master data is stored yet. Run refresh_amfi_master; this does not require an API key."
        ),
        "funds": funds,
    })
