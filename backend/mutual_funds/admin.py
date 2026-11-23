from django.contrib import admin

from .models import (
    MutualFundHolding,
    MutualFundNAV,
    MutualFundScheme,
    MutualFundTransaction,
    SIP,
    SIPInstallment,
)


@admin.register(MutualFundScheme)
class MutualFundSchemeAdmin(admin.ModelAdmin):

    list_display = (
        "scheme_name",
        "amc_name",
        "scheme_code",
        "plan",
        "option",
        "is_active",
    )

    list_filter = (
        "amc_name",
        "plan",
        "option",
        "is_active",
    )

    search_fields = (
        "scheme_name",
        "scheme_code",
        "isin_growth",
        "isin_dividend",
        "amc_name",
    )


@admin.register(MutualFundNAV)
class MutualFundNAVAdmin(admin.ModelAdmin):

    list_display = (
        "scheme",
        "date",
        "nav",
        "source",
    )

    list_filter = (
        "source",
        "date",
    )

    search_fields = (
        "scheme__scheme_name",
        "scheme__scheme_code",
    )

    date_hierarchy = "date"


@admin.register(MutualFundTransaction)
class MutualFundTransactionAdmin(admin.ModelAdmin):

    list_display = (
        "scheme",
        "transaction_type",
        "transaction_date",
        "units",
        "nav",
        "amount",
        "fees",
    )

    list_filter = (
        "transaction_type",
        "transaction_date",
    )

    search_fields = (
        "scheme__scheme_name",
        "scheme__scheme_code",
    )

    date_hierarchy = "transaction_date"


@admin.register(SIP)
class SIPAdmin(admin.ModelAdmin):

    list_display = (
        "scheme",
        "amount",
        "frequency",
        "start_date",
        "next_installment_date",
        "is_active",
    )

    list_filter = (
        "frequency",
        "is_active",
    )

    search_fields = (
        "scheme__scheme_name",
        "scheme__scheme_code",
    )


@admin.register(MutualFundHolding)
class MutualFundHoldingAdmin(admin.ModelAdmin):

    list_display = (
        "scheme",
        "units",
        "invested_value",
        "average_nav",
        "current_nav",
        "current_value",
        "unrealized_pnl",
        "updated_at",
    )

    search_fields = (
        "scheme__scheme_name",
        "scheme__scheme_code",
    )

@admin.register(SIPInstallment)
class SIPInstallmentAdmin(admin.ModelAdmin):

    list_display = (
        "sip",
        "scheduled_date",
        "amount",
        "status",
        "transaction",
        "executed_at",
    )

    list_filter = (
        "status",
        "scheduled_date",
    )

    search_fields = (
        "sip__scheme__scheme_name",
        "sip__scheme__scheme_code",
    )

    date_hierarchy = "scheduled_date"

from .master_models import (
    MFBenchmarkTRI, MFExternalPortfolioSnapshot, MFMarketCapClassification,
    MFMasterData, MFMasterHolding, MFSourceRun,
)


@admin.register(MFMasterData)
class MFMasterDataAdmin(admin.ModelAdmin):
    list_display = ("scheme_code", "scheme_name", "amc", "as_of_date", "aum_type", "aum_cr")
    list_filter = ("as_of_date", "aum_type", "sebi_category_level_1", "sebi_category_level_3", "amc")
    search_fields = ("scheme_code", "scheme_name", "isin_growth", "isin_idcw", "amc")
    date_hierarchy = "as_of_date"


@admin.register(MFMasterHolding)
class MFMasterHoldingAdmin(admin.ModelAdmin):
    list_display = ("scheme", "portfolio_date", "portfolio_date_basis", "security_name", "isin", "ticker", "instrument_type", "pct_of_nav", "source")
    list_filter = ("portfolio_date", "portfolio_date_basis", "instrument_type", "source", "rating")
    search_fields = ("scheme__scheme_code", "scheme__scheme_name", "security_name", "isin", "ticker")
    date_hierarchy = "portfolio_date"


@admin.register(MFSourceRun)
class MFSourceRunAdmin(admin.ModelAdmin):
    list_display = ("source", "command", "as_of_date", "status", "rows_parsed", "rows_inserted", "rows_updated", "started_at")
    list_filter = ("source", "status", "as_of_date")
    search_fields = ("source", "command", "source_url", "raw_file_path")
    date_hierarchy = "started_at"
    readonly_fields = tuple(field.name for field in MFSourceRun._meta.fields)


@admin.register(MFBenchmarkTRI)
class MFBenchmarkTRIAdmin(admin.ModelAdmin):
    list_display = ("index_code", "index_name", "date", "tri_value", "source", "fetched_at")
    list_filter = ("index_code", "source", "date")
    search_fields = ("index_code", "index_name")
    date_hierarchy = "date"


@admin.register(MFMarketCapClassification)
class MFMarketCapClassificationAdmin(admin.ModelAdmin):
    list_display = ("isin", "company_name", "market_cap_class", "market_cap_rank", "effective_period")
    list_filter = ("effective_period", "market_cap_class")
    search_fields = ("isin", "company_name")


@admin.register(MFExternalPortfolioSnapshot)
class MFExternalPortfolioSnapshotAdmin(admin.ModelAdmin):
    list_display = ("scheme", "provider", "observed_at", "provider_as_of_date", "aum_cr", "expense_ratio_pct", "holdings_count")
    list_filter = ("provider", "observed_at", "category")
    search_fields = ("scheme__scheme_code", "scheme__scheme_name", "fund_house", "scheme_name")
    date_hierarchy = "observed_at"
    readonly_fields = tuple(field.name for field in MFExternalPortfolioSnapshot._meta.fields)
