from django.contrib import admin

from mf_intelligence.models import MFBenchmark, MFCategory, MFScheme, MFStockClassification, MFSyncRun


@admin.register(MFCategory)
class MFCategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "asset_class", "total_schemes", "metrics_as_of", "metrics_synced_at")
    list_filter = ("asset_class",)
    search_fields = ("name", "slug")


@admin.register(MFScheme)
class MFSchemeAdmin(admin.ModelAdmin):
    list_display = ("name", "amfi_code", "category", "plan", "option", "avg_aum_crore", "latest_nav_date", "is_active")
    list_filter = ("plan", "option", "is_active", "category__asset_class")
    search_fields = ("name", "amfi_code", "isin", "amc")
    raw_id_fields = ("category",)


@admin.register(MFBenchmark)
class MFBenchmarkAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "asset_type", "latest_date", "return_1y", "synced_at")


@admin.register(MFStockClassification)
class MFStockClassificationAdmin(admin.ModelAdmin):
    list_display = ("isin", "company_name", "cap_bucket", "rank", "source", "period_end")
    list_filter = ("cap_bucket", "source")
    search_fields = ("isin", "company_name")


@admin.register(MFSyncRun)
class MFSyncRunAdmin(admin.ModelAdmin):
    list_display = ("id", "status", "trigger", "started_at", "finished_at", "current_step")
    list_filter = ("status", "trigger")
    readonly_fields = ("stats", "errors", "scope")
