from django.urls import path

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
    watch_list_toggle,
)

urlpatterns = [
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
]
