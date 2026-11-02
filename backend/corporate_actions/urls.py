from django.urls import path

from .views import (\n    corporate_action_candidates, corporate_action_detail, corporate_action_list,\n    corporate_action_notifications, corporate_action_notification_update,\n    corporate_action_reconciliation_update,\n)

urlpatterns = [
    path("", corporate_action_list, name="corporate-action-list"),
    path("candidates/", corporate_action_candidates, name="corporate-action-candidates"),\n    path("notifications/", corporate_action_notifications, name="corporate-action-notifications"),\n    path("notifications/<int:notification_id>/", corporate_action_notification_update, name="corporate-action-notification-update"),\n    path("reconciliations/<int:reconciliation_id>/decision/", corporate_action_reconciliation_update, name="corporate-action-reconciliation-decision"),
    path("<int:action_id>/", corporate_action_detail, name="corporate-action-detail"),
]
