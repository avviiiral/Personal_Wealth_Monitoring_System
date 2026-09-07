from django.urls import path

from .views import (
    portfolio_assets,
    portfolio_asset_detail,
    portfolio_transactions,
    portfolio_transaction_detail,
    portfolio_summary,
    portfolio_holdings,
    portfolio_tree,
)

from .manual_price_views import (
    manual_asset_price,
)


urlpatterns = [
    path(
        "assets/",
        portfolio_assets,
        name="portfolio-assets",
    ),

    path(
        "assets/<int:asset_id>/",
        portfolio_asset_detail,
        name="portfolio-asset-detail",
    ),

    path(
        "transactions/",
        portfolio_transactions,
        name="portfolio-transactions",
    ),

    path(
        "transactions/<int:transaction_id>/",
        portfolio_transaction_detail,
        name="portfolio-transaction-detail",
    ),

    path(
        "summary/",
        portfolio_summary,
        name="portfolio-summary",
    ),

    path(
        "holdings/",
        portfolio_holdings,
        name="portfolio-holdings",
    ),

    path(
        "tree/",
        portfolio_tree,
        name="portfolio-tree",
    ),

    path(
        "assets/<int:asset_id>/manual-price/",
        manual_asset_price,
        name="manual-asset-price",
    ),
]