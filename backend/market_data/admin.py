from django.contrib import admin

from .models import MarketPrice


@admin.register(MarketPrice)
class MarketPriceAdmin(admin.ModelAdmin):

    list_display = (
        "asset",
        "date",
        "open_price",
        "high_price",
        "low_price",
        "close_price",
        "adjusted_close",
        "volume",
        "source",
    )

    list_filter = (
        "source",
        "date",
    )

    search_fields = (
        "asset__name",
        "asset__symbol",
    )

    date_hierarchy = "date"

    ordering = (
        "-date",
        "asset",
    )