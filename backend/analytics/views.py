from datetime import date, timedelta

from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .services.investment_summary import InvestmentSummaryService
from .services.portfolio_analytics import PortfolioAnalytics
from .services.unified_wealth import UnifiedWealthAnalytics
from .services.equity_analysis import EquityAnalysisService

from users.permissions import get_visible_owner_ids


# ==========================================================
# EXISTING ANALYTICS ENDPOINTS
# ==========================================================

@api_view(["GET"])
@permission_classes([IsAuthenticated])
def analytics_summary(request):
    data = PortfolioAnalytics.calculate_summary(get_visible_owner_ids(request.user))
    return Response(data)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def analytics_allocation(request):
    data = PortfolioAnalytics.calculate_allocation(get_visible_owner_ids(request.user))
    return Response({"results": data})


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def analytics_performance(request):
    data = PortfolioAnalytics.get_performance_ranking(get_visible_owner_ids(request.user))
    return Response({"results": data})


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def analytics_historical(request):
    """Return historical portfolio values."""
    try:
        days = int(request.GET.get("days", 30))
    except (TypeError, ValueError):
        days = 30

    days = max(1, min(days, 3650))
    end_date = date.today()
    start_date = end_date - timedelta(days=days - 1)
    results = []
    current_date = start_date
    owner_ids = get_visible_owner_ids(request.user)

    while current_date <= end_date:
        result = PortfolioAnalytics.calculate_historical_value(owner_ids, current_date)
        results.append({
            "date": result["date"],
            "invested_value": result["invested_value"],
            "portfolio_value": result["portfolio_value"],
            "pnl": result["pnl"],
        })
        current_date += timedelta(days=1)

    return Response({
        "days": days,
        "start_date": start_date,
        "end_date": end_date,
        "results": results,
    })


# ==========================================================
# UNIFIED WEALTH ANALYTICS
# ==========================================================

@api_view(["GET"])
@permission_classes([IsAuthenticated])
def wealth_summary(request):
    family_name = request.GET.get("family") or None
    data = UnifiedWealthAnalytics.calculate_summary(
        get_visible_owner_ids(request.user), family_name=family_name
    )
    return Response(data)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def wealth_allocation(request):
    data = UnifiedWealthAnalytics.calculate_allocation(get_visible_owner_ids(request.user))
    return Response({"results": data})


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def wealth_performance(request):
    data = UnifiedWealthAnalytics.calculate_performance(get_visible_owner_ids(request.user))
    return Response({"results": data})


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def wealth_xirr(request):
    family_name = request.GET.get("family") or None
    data = UnifiedWealthAnalytics.calculate_xirr(
        get_visible_owner_ids(request.user), family_name=family_name
    )
    return Response({"xirr_percentage": data})


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def wealth_investment_summary(request):
    family_name = request.GET.get("family") or None
    data = InvestmentSummaryService.calculate(
        get_visible_owner_ids(request.user), family_name=family_name
    )
    return Response(data)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def wealth_performance_by_subclass(request):
    data = InvestmentSummaryService.calculate_performance_by_subclass(
        get_visible_owner_ids(request.user)
    )
    return Response({"results": data})


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def wealth_allocation_by_advisor(request):
    data = InvestmentSummaryService.calculate_allocation_by_advisor(
        get_visible_owner_ids(request.user)
    )
    return Response(data)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def wealth_performance_by_advisor(request):
    data = InvestmentSummaryService.calculate_performance_by_advisor(
        get_visible_owner_ids(request.user)
    )
    return Response({"results": data})


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def wealth_historical(request):
    from .services.historical_wealth import HistoricalWealthAnalytics

    try:
        days = int(request.GET.get("days", 30))
    except (TypeError, ValueError):
        days = 30

    days = max(1, min(days, 3650))
    family_name = request.GET.get("family") or None
    end_date = date.today()
    start_date = end_date - timedelta(days=days - 1)
    results = HistoricalWealthAnalytics.calculate_history(
        get_visible_owner_ids(request.user),
        start_date,
        end_date,
        family_name=family_name,
    )
    return Response({
        "days": days,
        "start_date": start_date,
        "end_date": end_date,
        "results": results,
    })


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def wealth_composition_by_amc(request):
    data = InvestmentSummaryService.calculate_composition_by_amc(
        get_visible_owner_ids(request.user)
    )
    return Response(data)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def wealth_equity_analysis(request):
    """Return equity valuation metrics including value-weighted PEG."""
    data = EquityAnalysisService.calculate(get_visible_owner_ids(request.user))
    return Response(data)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def wealth_fixed_income_analysis(request):
    data = InvestmentSummaryService.calculate_fixed_income_analysis(
        get_visible_owner_ids(request.user)
    )
    return Response(data)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def wealth_sector_allocation(request):
    data = InvestmentSummaryService.calculate_sector_allocation(
        get_visible_owner_ids(request.user)
    )
    return Response(data)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def wealth_market_cap_allocation(request):
    data = InvestmentSummaryService.calculate_market_cap_allocation(
        get_visible_owner_ids(request.user)
    )
    return Response(data)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def wealth_non_stock_holding_types(request):
    data = InvestmentSummaryService.calculate_non_stock_holding_types(
        get_visible_owner_ids(request.user)
    )
    return Response(data)
