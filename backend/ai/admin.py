from django.contrib import admin

from .models import GeminiUsageLog


@admin.register(GeminiUsageLog)
class GeminiUsageLogAdmin(admin.ModelAdmin):

    list_display = (
        "id",
        "endpoint",
        "user",
        "model_name",
        "prompt_tokens",
        "output_tokens",
        "total_tokens",
        "cached_tokens",
        "created_at",
    )

    list_filter = (
        "endpoint",
        "model_name",
    )

    search_fields = (
        "user__username",
    )

    ordering = ("-created_at",)
