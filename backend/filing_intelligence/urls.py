from django.urls import path
from .views import filing_detail
urlpatterns=[path("<int:filing_id>/",filing_detail,name="filing-detail")]
