from django.urls import path

from fund_intelligence.views import fund_intelligence_snapshot

urlpatterns = [
    path("snapshot/", fund_intelligence_snapshot, name="fund-intelligence-snapshot"),
]
