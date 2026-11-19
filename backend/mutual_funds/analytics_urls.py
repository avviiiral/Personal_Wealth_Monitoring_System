from django.urls import path

from watchlist.analytics import mutual_fund_analytics_overview
from watchlist.indianapi_reports import (
    india_mutual_fund_feed,
    indianapi_usage_status,
    refresh_india_mutual_fund_feed,
)
from watchlist.research_reports import latest_mutual_fund_research_report


urlpatterns = [
    path("overview/", mutual_fund_analytics_overview, name="mutual-fund-analytics-overview"),
    path("research-report/", latest_mutual_fund_research_report, name="mutual-fund-research-report"),
    path("india-market-feed/", india_mutual_fund_feed, name="india-mutual-fund-market-feed"),
    path("api-usage/", indianapi_usage_status, name="indianapi-usage-status"),
    path("refresh/", refresh_india_mutual_fund_feed, name="mutual-fund-analytics-refresh"),
]
