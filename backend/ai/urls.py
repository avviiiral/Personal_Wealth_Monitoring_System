from django.urls import include, path

from .views import portfolio_chat


urlpatterns = [
    path(
        "chat/",
        portfolio_chat,
        name="portfolio-chat",
    ),

    path(
        "",
        include("portfolio_news.urls"),
    ),
]