from django.urls import path

from .views import (
    corporate_action_accept,
    corporate_action_candidates,
    corporate_action_detail,
    corporate_action_list,
    corporate_action_notifications,
    corporate_action_notification_update,
    corporate_action_reconciliation_update,
    corporate_action_identity_decision,
    corporate_action_refresh,
    corporate_action_reject,
)

urlpatterns = [
    path("", corporate_action_list, name="corporate-action-list"),
    path("candidates/", corporate_action_candidates, name="corporate-action-candidates"),
    path("notifications/", corporate_action_notifications, name="corporate-action-notifications"),
    path("notifications/<int:notification_id>/", corporate_action_notification_update, name="corporate-action-notification-update"),
    path("reconciliations/<int:reconciliation_id>/decision/", corporate_action_reconciliation_update, name="corporate-action-reconciliation-decision"),
    path("reconciliations/<int:reconciliation_id>/identity-decision/", corporate_action_identity_decision, name="corporate-action-identity-decision"),
    path("reconciliations/<int:reconciliation_id>/accept/", corporate_action_accept, name="corporate-action-accept"),
    path("reconciliations/<int:reconciliation_id>/reject/", corporate_action_reject, name="corporate-action-reject"),
    path("reconciliations/<int:reconciliation_id>/refresh/", corporate_action_refresh, name="corporate-action-refresh"),
    path("<int:action_id>/", corporate_action_detail, name="corporate-action-detail"),
]
