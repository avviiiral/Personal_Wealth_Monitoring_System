from django.urls import path

from .views import (
    import_transactions,
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