from django.urls import path

from .views import corporate_action_candidates, corporate_action_detail, corporate_action_list

urlpatterns = [
    path("", corporate_action_list, name="corporate-action-list"),
    path("candidates/", corporate_action_candidates, name="corporate-action-candidates"),
    path("<int:action_id>/", corporate_action_detail, name="corporate-action-detail"),
]
