from django.urls import path

from .views import (
    analytics_allocation,
    analytics_historical,
    analytics_performance,
    analytics_summary,
    wealth_allocation,
    wealth_allocation_by_advisor,
    wealth_composition_by_amc,
    wealth_equity_analysis,
    wealth_fixed_income_analysis,
    wealth_investment_summary,
    wealth_market_cap_allocation,
    wealth_non_stock_holding_types,
    wealth_performance,
    wealth_performance_by_advisor,
    wealth_performance_by_subclass,
    wealth_sector_allocation,
    wealth_summary,
    wealth_xirr,
    wealth_historical,
)


urlpatterns = [
    # Existing analytics APIs
    path(
        "summary/",
        analytics_summary,
        name="analytics-summary",
    ),

    path(
        "allocation/",
        analytics_allocation,
        name="analytics-allocation",
    ),

    path(
        "performance/",
        analytics_performance,
        name="analytics-performance",
    ),

    path(
        "historical/",
        analytics_historical,
        name="analytics-historical",
    ),

    # Unified wealth APIs
    path(
        "wealth/summary/",
        wealth_summary,
        name="wealth-summary",
    ),

    path(
        "wealth/allocation/",
        wealth_allocation,
        name="wealth-allocation",
    ),

    path(
        "wealth/performance/",
        wealth_performance,
        name="wealth-performance",
    ),

    path(
        "wealth/xirr/",
        wealth_xirr,
        name="wealth-xirr",
    ),

    path(
        "wealth/investment-summary/",
        wealth_investment_summary,
        name="wealth-investment-summary",
    ),

    path(
        "wealth/performance-by-subclass/",
        wealth_performance_by_subclass,
        name="wealth-performance-by-subclass",
    ),

    path(
        "wealth/allocation-by-advisor/",
        wealth_allocation_by_advisor,
        name="wealth-allocation-by-advisor",
    ),

    path(
        "wealth/composition-by-amc/",
        wealth_composition_by_amc,
        name="wealth-composition-by-amc",
    ),

    path(
        "wealth/equity-analysis/",
        wealth_equity_analysis,
        name="wealth-equity-analysis",
    ),

    path(
        "wealth/fixed-income-analysis/",
        wealth_fixed_income_analysis,
        name="wealth-fixed-income-analysis",
    ),

    path(
        "wealth/sector-allocation/",
        wealth_sector_allocation,
        name="wealth-sector-allocation",
    ),

    path(
        "wealth/market-cap-allocation/",
        wealth_market_cap_allocation,
        name="wealth-market-cap-allocation",
    ),

    path(
        "wealth/non-stock-holding-types/",
        wealth_non_stock_holding_types,
        name="wealth-non-stock-holding-types",
    ),

    path(
        "wealth/performance-by-advisor/",
        wealth_performance_by_advisor,
        name="wealth-performance-by-advisor",
    ),

    path(
        "wealth/historical/",
        wealth_historical,
        name="wealth-historical",
    ),
]