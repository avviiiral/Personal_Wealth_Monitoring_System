from datetime import date, timedelta

from decimal import Decimal, InvalidOperation

from django.views.decorators.csrf import ensure_csrf_cookie
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status

from .models import StandardAllocation
from .services.investment_summary import InvestmentSummaryService
from .services.portfolio_analytics import PortfolioAnalytics
from .services.unified_wealth import UnifiedWealthAnalytics
from .services.equity_analysis import EquityAnalysisService
from .services.mutual_fund_lookthrough import MutualFundLookThroughService



@api_view(["GET"])
@permission_classes([IsAuthenticated])
def analytics_summary(request):
    return Response(PortfolioAnalytics.calculate_summary(request.user))


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def analytics_allocation(request):
    owner_ids = request.user
    direct_holdings = list(PortfolioAnalytics.get_holdings(owner_ids))
    return Response({"results": MutualFundLookThroughService.allocation(owner_ids, direct_holdings)})


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def analytics_performance(request):
    return Response({"results": PortfolioAnalytics.get_performance_ranking(request.user)})


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def analytics_historical(request):
    try:
        days = int(request.GET.get("days", 30))
    except (TypeError, ValueError):
        days = 30
    days = max(1, min(days, 3650))
    end_date = date.today()
    start_date = end_date - timedelta(days=days - 1)
    results = []
    current_date = start_date
    owner_ids = request.user
    while current_date <= end_date:
        result = PortfolioAnalytics.calculate_historical_value(owner_ids, current_date)
        results.append({"date": result["date"], "invested_value": result["invested_value"], "portfolio_value": result["portfolio_value"], "pnl": result["pnl"]})
        current_date += timedelta(days=1)
    return Response({"days": days, "start_date": start_date, "end_date": end_date, "results": results})


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def wealth_summary(request):
    family_name = request.GET.get("family") or None
    return Response(UnifiedWealthAnalytics.calculate_summary(request.user, family_name=family_name))


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def wealth_allocation(request):
    owner_ids = request.user
    direct_holdings = list(PortfolioAnalytics.get_holdings(owner_ids))
    return Response({"results": MutualFundLookThroughService.allocation(owner_ids, direct_holdings)})


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def wealth_performance(request):
    return Response({"results": UnifiedWealthAnalytics.calculate_performance(request.user)})


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def wealth_xirr(request):
    family_name = request.GET.get("family") or None
    data = UnifiedWealthAnalytics.calculate_xirr(request.user, family_name=family_name)
    return Response({"xirr_percentage": data})


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def wealth_investment_summary(request):
    family_name = request.GET.get("family") or None
    return Response(InvestmentSummaryService.calculate(request.user, family_name=family_name))


@ensure_csrf_cookie
@api_view(["GET"])
@permission_classes([IsAuthenticated])
def wealth_standard_allocations(request):
    family_name = request.GET.get("family") or ""
    rows = StandardAllocation.objects.filter(
        user=request.user,
        family_name=family_name,
    )
    return Response({
        "family": family_name,
        "allocations": {
            row.asset_category: float(row.allocation_percent)
            for row in rows
        },
    })


@api_view(["PUT"])
@permission_classes([IsAuthenticated])
def wealth_standard_allocations_update(request):
    family_name = request.GET.get("family") or ""
    raw_allocations = request.data.get("allocations")

    if not isinstance(raw_allocations, dict) or not raw_allocations:
        return Response(
            {"detail": "Allocations must be a non-empty object."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    allocations = {}
    try:
        for category, raw_value in raw_allocations.items():
            category = str(category).strip()
            value = Decimal(str(raw_value))
            if not category or value < 0 or value > 100:
                raise ValueError
            allocations[category] = value.quantize(Decimal("0.01"))
    except (InvalidOperation, TypeError, ValueError):
        return Response(
            {"detail": "Each Standard Allocation must be a number between 0 and 100."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    total = sum(allocations.values(), Decimal("0"))
    if total != Decimal("100.00"):
        return Response(
            {"detail": f"Standard Allocation must total exactly 100%. Current total is {total}%."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    StandardAllocation.objects.filter(
        user=request.user,
        family_name=family_name,
    ).delete()

    StandardAllocation.objects.bulk_create([
        StandardAllocation(
            user=request.user,
            family_name=family_name,
            asset_category=category,
            allocation_percent=value,
        )
        for category, value in allocations.items()
    ])

    return Response({
        "family": family_name,
        "allocations": {category: float(value) for category, value in allocations.items()},
    })


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def wealth_performance_by_subclass(request):
    data = InvestmentSummaryService.calculate_performance_by_subclass(request.user)
    return Response({"results": data})


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def wealth_allocation_by_advisor(request):
    return Response(InvestmentSummaryService.calculate_allocation_by_advisor(request.user))


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def wealth_performance_by_advisor(request):
    return Response({"results": InvestmentSummaryService.calculate_performance_by_advisor(request.user)})


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def wealth_historical_period(request):
    from .services.historical_wealth import HistoricalWealthAnalytics

    period = request.GET.get("period", "this-month")
    family_name = request.GET.get("family") or None
    today = date.today()

    if period == "this-month":
        start_date = today.replace(day=1)
        end_date = today
    elif period == "last-month":
        current_month_start = today.replace(day=1)
        end_date = current_month_start - timedelta(days=1)
        start_date = end_date.replace(day=1)
    elif period == "inception":
        start_date = HistoricalWealthAnalytics.get_inception_date(
            request.user,
            family_name=family_name,
        ) or today
        end_date = today
    else:
        return Response(
            {"detail": "Unsupported historical period."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    results = HistoricalWealthAnalytics.calculate_history(
        request.user,
        start_date,
        end_date,
        family_name=family_name,
    )
    return Response({
        "period": period,
        "days": (end_date - start_date).days + 1,
        "start_date": start_date,
        "end_date": end_date,
        "results": results,
    })


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def wealth_historical(request):
    from .services.historical_wealth import HistoricalWealthAnalytics
    family_name = request.GET.get("family") or None
    start_date_param = request.GET.get("start_date")
    end_date_param = request.GET.get("end_date")

    if start_date_param or end_date_param:
        try:
            start_date = date.fromisoformat(start_date_param)
            end_date = date.fromisoformat(end_date_param)
        except (TypeError, ValueError):
            return Response(
                {"detail": "start_date and end_date must be valid ISO dates."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if start_date > end_date:
            return Response(
                {"detail": "start_date cannot be after end_date."},
                status=status.HTTP_400_BAD_REQUEST,
            )
    else:
        try:
            days = int(request.GET.get("days", 30))
        except (TypeError, ValueError):
            days = 30
        days = max(1, min(days, 3650))
        end_date = date.today()
        start_date = end_date - timedelta(days=days - 1)

    results = HistoricalWealthAnalytics.calculate_history(
        request.user,
        start_date,
        end_date,
        family_name=family_name,
    )
    return Response({
        "days": (end_date - start_date).days + 1,
        "start_date": start_date,
        "end_date": end_date,
        "results": results,
    })


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def wealth_composition_by_amc(request):
    return Response(InvestmentSummaryService.calculate_composition_by_amc(request.user))


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def wealth_equity_analysis(request):
    return Response(EquityAnalysisService.calculate(request.user))


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def wealth_fixed_income_analysis(request):
    return Response(InvestmentSummaryService.calculate_fixed_income_analysis(request.user))


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def wealth_sector_allocation(request):
    owner_ids = request.user
    direct_holdings = list(UnifiedWealthAnalytics.get_equity_holdings(owner_ids))
    asset_class_by_asset_id = InvestmentSummaryService._equity_asset_class_by_asset_id(owner_ids)
    equity_asset_ids = {
        asset_id
        for asset_id, raw_class in asset_class_by_asset_id.items()
        if InvestmentSummaryService._normalize_asset_class(raw_class) in InvestmentSummaryService.EQUITY_ASSET_CLASSES
    }
    data = MutualFundLookThroughService.sector_allocation(owner_ids, direct_holdings, equity_asset_ids)
    return Response(data)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def wealth_market_cap_allocation(request):
    return Response(InvestmentSummaryService.calculate_market_cap_allocation(request.user))


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def wealth_non_stock_holding_types(request):
    return Response(InvestmentSummaryService.calculate_non_stock_holding_types(request.user))
