from django.urls import path

from .views import (
    portfolio_assets,
    portfolio_asset_detail,
    portfolio_transactions,
    portfolio_transaction_detail,
    portfolio_transaction_edit_history,
    portfolio_summary,
    portfolio_holdings,
    portfolio_tree,
    portfolio_asset_underlying_import,
)
from .holding_report_views import holding_report

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
        "transactions/edit-history/",
        portfolio_transaction_edit_history,
        name="portfolio-transaction-edit-history",
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
        "holding-report/",
        holding_report,
        name="holding-report",
    ),

    path(
        "tree/",
        portfolio_tree,
        name="portfolio-tree",
    ),

    path(
        "assets/<int:asset_id>/underlying/import/",
        portfolio_asset_underlying_import,
        name="portfolio-asset-underlying-import",
    ),

    path(
        "assets/<int:asset_id>/manual-price/",
        manual_asset_price,
        name="manual-asset-price",
    ),
]
