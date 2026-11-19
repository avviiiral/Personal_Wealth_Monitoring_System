from django.urls import path

from watchlist.analytics import mutual_fund_analytics_overview
from watchlist.research_reports import latest_mutual_fund_research_report
from watchlist.indianapi_reports import india_mutual_fund_feed, indianapi_usage_status, refresh_india_mutual_fund_feed

from watchlist.views import (
    watch_list_bulk_add,
    watch_list_bulk_remove,
    watch_list_filters,
    watch_list_performance,
    watch_list_product_detail,
    watch_list_benchmark,
    watch_list_benchmark_performance,
    watch_list_benchmarks_performance,
    watch_list_products,
    watch_list_refresh,
    watch_list_refresh_status,
    watch_list_toggle,
)

urlpatterns = [
    path("analytics/overview/", mutual_fund_analytics_overview, name="mutual-fund-analytics-overview"),
    path("analytics/research-report/", latest_mutual_fund_research_report, name="mutual-fund-research-report"),
    path("analytics/india-market-feed/", india_mutual_fund_feed, name="india-mutual-fund-market-feed"),
    path("analytics/api-usage/", indianapi_usage_status, name="indianapi-usage-status"),
    path("analytics/refresh/", refresh_india_mutual_fund_feed, name="mutual-fund-analytics-refresh"),
    path("filters/", watch_list_filters, name="watch-list-filters"),
    path("products/", watch_list_products, name="watch-list-products"),
    path("products/<int:product_id>/", watch_list_product_detail, name="watch-list-product-detail"),
    path("products/<int:product_id>/performance/", watch_list_performance, name="watch-list-performance"),
    path("products/<int:product_id>/benchmark/", watch_list_benchmark, name="watch-list-benchmark"),
    path("products/<int:product_id>/benchmark-performance/", watch_list_benchmark_performance, name="watch-list-benchmark-performance"),
    path("benchmarks-performance/", watch_list_benchmarks_performance, name="watch-list-benchmarks-performance"),
    path("products/<int:product_id>/toggle/", watch_list_toggle, name="watch-list-toggle"),
    path("bulk-add/", watch_list_bulk_add, name="watch-list-bulk-add"),
    path("bulk-remove/", watch_list_bulk_remove, name="watch-list-bulk-remove"),
    path("refresh/", watch_list_refresh, name="watch-list-refresh"),
    path("refresh/status/", watch_list_refresh_status, name="watch-list-refresh-status"),
]
