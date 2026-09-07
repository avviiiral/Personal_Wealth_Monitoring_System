from django.contrib import admin
from django.urls import include, path

from market_data.views import stock_search


urlpatterns = [

    path(
        "admin/",
        admin.site.urls,
    ),

    # General API
    path(
        "api/",
        include("api.urls"),
    ),

    # RBAC / User Management / Settings-scoped prices
    path(
        "api/settings/",
        include("users.urls"),
    ),

    # Portfolio API
    path(
        "api/portfolio/",
        include("portfolio.urls"),
    ),

    # Analytics API
    path(
        "api/analytics/",
        include("analytics.urls"),
    ),

    # Mutual Funds API
    path(
        "api/mutual-funds/",
        include("mutual_funds.urls"),
    ),

    # Market Data API
    path(
        "api/market-data/stocks/search/",
        stock_search,
        name="stock-search",
    ),

    # AI Portfolio Chat
    path(
        "api/ai/",
        include("ai.urls"),
    ),
    
    # Transaction Import API
    path(
        "api/investments/",
        include("investments.urls"),
    ),
]