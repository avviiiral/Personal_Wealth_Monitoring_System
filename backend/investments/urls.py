from django.urls import path

from .views import (
    import_transactions,
    transaction_upload_history,
    transaction_upload_detail,
    transaction_underlyings,
    download_transaction_template,
    security_master_list,
    security_master_detail,
)


urlpatterns = [
    path(
        "import/",
        import_transactions,
        name="import-transactions",
    ),

    path(
        "upload-history/",
        transaction_upload_history,
        name="transaction-upload-history",
    ),

    path(
        "upload-history/<int:upload_id>/",
        transaction_upload_detail,
        name="transaction-upload-detail",
    ),

    path(
        "underlyings/",
        transaction_underlyings,
        name="transaction-underlyings",
    ),

    path(
        "transaction-template/",
        download_transaction_template,
        name="transaction-template",
    ),

    path(
        "security-master/",
        security_master_list,
        name="security-master-list",
    ),

    path(
        "security-master/<int:security_id>/",
        security_master_detail,
        name="security-master-detail",
    ),
]