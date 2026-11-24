from django.urls import path

from mf_intelligence.views import (
    mf_intelligence_categories,
    mf_intelligence_dashboard,
    mf_intelligence_scheme,
    mf_intelligence_sync,
    mf_intelligence_sync_status,
)

urlpatterns = [
    path("categories/", mf_intelligence_categories, name="mf-intelligence-categories"),
    path("categories/<slug:slug>/dashboard/", mf_intelligence_dashboard, name="mf-intelligence-dashboard"),
    path("schemes/<str:code>/", mf_intelligence_scheme, name="mf-intelligence-scheme"),
    path("sync/", mf_intelligence_sync, name="mf-intelligence-sync"),
    path("sync/status/", mf_intelligence_sync_status, name="mf-intelligence-sync-status"),
]
