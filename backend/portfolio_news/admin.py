from django.contrib import admin

from .models import (
    NewsArticle,
    NewsArticleSource,
    PortfolioNewsAlert,
)


class NewsArticleSourceInline(admin.TabularInline):
    model = NewsArticleSource
    extra = 0
    fields = (
        "publisher_name",
        "url",
        "quality_tier",
        "published_at",
        "first_seen_at",
    )
    readonly_fields = ("first_seen_at",)


@admin.register(NewsArticle)
class NewsArticleAdmin(admin.ModelAdmin):

    list_display = (
        "id",
        "title",
        "source",
        "source_quality",
        "source_count",
        "published_at",
        "created_at",
    )

    search_fields = (
        "title",
        "normalized_title",
        "source",
    )

    list_filter = (
        "source",
        "source_quality",
    )

    ordering = ("-published_at",)

    inlines = [NewsArticleSourceInline]


@admin.register(PortfolioNewsAlert)
class PortfolioNewsAlertAdmin(admin.ModelAdmin):

    list_display = (
        "id",
        "user",
        "holding_display_name",
        "category",
        "impact",
        "notification_tier",
        "alert_score",
        "is_read",
        "notification_sent",
        "created_at",
    )

    list_filter = (
        "notification_tier",
        "impact",
        "category",
        "is_read",
    )

    search_fields = (
        "holding_display_name",
        "user__username",
    )

    ordering = ("-alert_score", "-created_at")