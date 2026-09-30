from django.contrib import admin
from .models import Filing

@admin.register(Filing)
class FilingAdmin(admin.ModelAdmin):
    list_display = ("exchange", "symbol", "subject", "event_type", "severity", "published_at", "processing_status")
    list_filter = ("exchange", "severity", "processing_status", "event_type")
    search_fields = ("company_name", "symbol", "isin", "subject", "external_filing_id")
