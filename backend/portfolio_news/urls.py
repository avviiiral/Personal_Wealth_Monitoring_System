from django.urls import path

from .views import (
    mark_all_notifications_read,
    mark_notification_read,
    portfolio_news_detail,
    portfolio_news_digest,
    portfolio_news_list,
    portfolio_notifications_list,
)


urlpatterns = [

    path(
        "news/",
        portfolio_news_list,
        name="portfolio-news-list",
    ),

    path(
        "news/digest/",
        portfolio_news_digest,
        name="portfolio-news-digest",
    ),

    path(
        "news/<int:alert_id>/",
        portfolio_news_detail,
        name="portfolio-news-detail",
    ),

    path(
        "notifications/",
        portfolio_notifications_list,
        name="portfolio-news-notifications",
    ),

    path(
        "notifications/<int:alert_id>/read/",
        mark_notification_read,
        name="portfolio-news-notification-read",
    ),

    path(
        "notifications/read-all/",
        mark_all_notifications_read,
        name="portfolio-news-notifications-read-all",
    ),
]