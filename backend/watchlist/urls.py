from django.urls import path

from watchlist.views import (
    watch_list_filters,
    watch_list_performance,
    watch_list_product_detail,
    watch_list_products,
    watch_list_refresh,
    watch_list_toggle,
)

urlpatterns = [
    path("filters/", watch_list_filters, name="watch-list-filters"),
    path("products/", watch_list_products, name="watch-list-products"),
    path("products/<int:product_id>/", watch_list_product_detail, name="watch-list-product-detail"),
    path("products/<int:product_id>/performance/", watch_list_performance, name="watch-list-performance"),
    path("products/<int:product_id>/toggle/", watch_list_toggle, name="watch-list-toggle"),
    path("refresh/", watch_list_refresh, name="watch-list-refresh"),
]
