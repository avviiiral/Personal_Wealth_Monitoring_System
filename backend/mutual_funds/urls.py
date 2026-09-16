from django.urls import path

from .views import (
    csrf_token,
    mutual_fund_summary,
    mutual_fund_holdings,
    mutual_fund_transactions,
    mutual_fund_schemes,
    mutual_fund_transaction_create,
    sip_due,
    sip_execute,
    sip_list,
    sip_summary,
    sip_installment_execute,
    sip_create,
)
from .underlying_views import mutual_fund_underlying


urlpatterns = [
    path("summary/", mutual_fund_summary, name="mutual-fund-summary"),
    path("holdings/", mutual_fund_holdings, name="mutual-fund-holdings"),
    path("transactions/", mutual_fund_transactions, name="mutual-fund-transactions"),
    path("schemes/", mutual_fund_schemes, name="mutual-fund-schemes"),
    path("transactions/create/", mutual_fund_transaction_create, name="mutual-fund-transaction-create"),
    path("sips/", sip_list, name="sip-list"),
    path("sips/due/", sip_due, name="sip-due"),
    path("sips/summary/", sip_summary, name="sip-summary"),
    path("sips/<int:sip_id>/execute/", sip_execute, name="sip-execute"),
    path("sip-installments/<int:installment_id>/execute/", sip_installment_execute, name="sip-installment-execute"),
    path("sips/create/", sip_create, name="sip-create"),
    path("underlying/<int:scheme_id>/", mutual_fund_underlying, name="mutual-fund-underlying"),
    path("csrf/", csrf_token, name="csrf-token"),
]
