from django.urls import path

from watchlist.analytics import mutual_fund_analytics_overview
from watchlist.indianapi_reports import (
    india_mutual_fund_feed,
    indianapi_usage_status,
    refresh_india_mutual_fund_feed,
)
from watchlist.research_reports import (
    latest_mutual_fund_research_report,
    mutual_fund_research_universes,
)
from mutual_funds.sebi_category_views import sebi_category_funds
from mutual_funds.sebi_views import (
    download_sebi_analytics_workbook,
    download_sebi_category_risk_chart,
    download_sebi_q1_chart,
    sebi_fund_nav_history,
    sebi_category_return_distribution,
    sebi_category_top_aaum,
    sebi_universe_rankings,
    sebi_universe_summary,
)


from .masterdata_api import mutual_fund_masterdata
from .charliealpha_api import (
    charliealpha_benchmarks,
    charliealpha_category_metrics,
    charliealpha_compare,
    charliealpha_fund_detail,
    charliealpha_overview,
)


urlpatterns = [
    path("masterdata/", mutual_fund_masterdata, name="mutual-fund-masterdata"),
    path("charliealpha/overview/", charliealpha_overview, name="charliealpha-mf-overview"),
    path("charliealpha/funds/<str:scheme_code>/", charliealpha_fund_detail, name="charliealpha-mf-fund-detail"),
    path("charliealpha/categories/<str:slug>/", charliealpha_category_metrics, name="charliealpha-mf-category-metrics"),
    path("charliealpha/benchmarks/", charliealpha_benchmarks, name="charliealpha-mf-benchmarks"),
    path("charliealpha/compare/", charliealpha_compare, name="charliealpha-mf-compare"),
    path("overview/", mutual_fund_analytics_overview, name="mutual-fund-analytics-overview"),
    path("research-report/", latest_mutual_fund_research_report, name="mutual-fund-research-report"),
    path("research-universes/", mutual_fund_research_universes, name="mutual-fund-research-universes"),
    path("india-market-feed/", india_mutual_fund_feed, name="india-mutual-fund-market-feed"),
    path("api-usage/", indianapi_usage_status, name="indianapi-usage-status"),
    path("refresh/", refresh_india_mutual_fund_feed, name="mutual-fund-analytics-refresh"),
    path("sebi/categories/", sebi_category_funds, name="sebi-mf-category-funds"),
    path("sebi/summary/", sebi_universe_summary, name="sebi-mf-universe-summary"),
    path("sebi/rankings/", sebi_universe_rankings, name="sebi-mf-universe-rankings"),
    path("sebi/category-return-distribution/", sebi_category_return_distribution, name="sebi-mf-category-return-distribution"),
    path("sebi/category-top-aaum/", sebi_category_top_aaum, name="sebi-mf-category-top-aaum"),
    path("sebi/funds/<str:scheme_code>/history/", sebi_fund_nav_history, name="sebi-mf-fund-nav-history"),
    path("sebi/export/", download_sebi_analytics_workbook, name="sebi-mf-analytics-export"),
    path("sebi/charts/category-risk/", download_sebi_category_risk_chart, name="sebi-mf-category-risk-chart"),
    path("sebi/charts/top-q1/", download_sebi_q1_chart, name="sebi-mf-top-q1-chart"),
]
