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