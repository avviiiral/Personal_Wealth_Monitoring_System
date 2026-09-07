from django.contrib import admin

from .models import (
    Asset,
    Holding,
    PortfolioPosition,
    SecurityMaster,
    Transaction,
)


@admin.register(Asset)
class AssetAdmin(admin.ModelAdmin):

    list_display = (
        "name",
        "category",
        "symbol",
        "isin",
        "institution",
        "currency",
        "security_master",
        "is_active",
        "created_at",
    )

    list_filter = (
        "category",
        "is_active",
        "currency",
    )

    search_fields = (
        "name",
        "symbol",
        "isin",
        "institution",
        "security_master__asset_name",
        "security_master__isin",
    )


@admin.register(SecurityMaster)
class SecurityMasterAdmin(admin.ModelAdmin):

    list_display = (
        "asset_name",
        "isin",
        "sector",
        "cap_type",
        "amc_name",
        "credit_rating",
        "manual_nav_enabled",
        "manual_nav",
        "updated_at",
    )

    list_filter = (
        "manual_nav_enabled",
        "sector",
        "cap_type",
        "amc_name",
        "credit_rating",
    )

    search_fields = (
        "asset_name",
        "isin",
        "sector",
        "cap_type",
        "amc_name",
    )

    fieldsets = (
        (
            None,
            {
                "fields": (
                    "owner",
                    "isin",
                    "asset_name",
                ),
            },
        ),
        (
            "Classification",
            {
                "fields": (
                    "sector",
                    "cap_type",
                    "amc_name",
                ),
            },
        ),
        (
            "Equity Quants",
            {
                "fields": (
                    "pe_ratio",
                    "pb_ratio",
                    "roe",
                ),
            },
        ),
        (
            "Fixed Income Quants",
            {
                "fields": (
                    "credit_rating",
                    "ytm",
                    "modified_duration",
                    "average_maturity",
                ),
            },
        ),
        (
            "Manual NAV",
            {
                "fields": (
                    "manual_nav_enabled",
                    "manual_nav",
                ),
            },
        ),
    )


@admin.register(Transaction)
class TransactionAdmin(admin.ModelAdmin):

    list_display = (
        "asset",
        "family_name",
        "portfolio",
        "asset_class",
        "sub_class",
        "transaction_type",
        "transaction_date",
        "quantity",
        "amount",
        "source",
    )

    list_filter = (
        "transaction_type",
        "transaction_date",
        "asset_class",
        "sub_class",
        "source",
    )

    search_fields = (
        "asset__name",
        "asset__symbol",
        "asset__isin",
        "family_name",
        "portfolio",
        "asset_class",
        "sub_class",
        "asset_name",
        "underlying",
        "advisors",
        "source_key",
    )

    date_hierarchy = "transaction_date"


@admin.register(Holding)
class HoldingAdmin(admin.ModelAdmin):

    list_display = (
        "asset",
        "quantity",
        "average_cost",
        "invested_value",
        "current_price",
        "current_value",
        "unrealized_pnl",
        "updated_at",
    )

    search_fields = (
        "asset__name",
        "asset__symbol",
        "asset__isin",
    )


@admin.register(PortfolioPosition)
class PortfolioPositionAdmin(admin.ModelAdmin):

    list_display = (
        "family_name",
        "portfolio",
        "asset",
        "quantity",
        "average_cost",
        "invested_value",
        "current_price",
        "current_value",
        "gain",
        "updated_at",
    )

    list_filter = (
        "family_name",
        "portfolio",
    )

    search_fields = (
        "family_name",
        "portfolio",
        "asset__name",
        "asset__symbol",
        "asset__isin",
    )