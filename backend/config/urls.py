from django.contrib import admin
from django.urls import include, path
from market_data.views import stock_search

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/", include("api.urls")),
    path("api/settings/", include("users.urls")),
    path("api/portfolio/", include("portfolio.urls")),
    path("api/analytics/", include("analytics.urls")),
    path("api/mutual-funds/", include("mutual_funds.urls")),
    path("api/market-data/stocks/search/", stock_search, name="stock-search"),
    path("api/ai/", include("ai.urls")),
    path("api/investments/", include("investments.urls")),
    path("api/watch-list/", include("watchlist.urls")),
]
