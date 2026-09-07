from django.apps import AppConfig

from config.scheduler_guard import should_start_background_schedulers


class PortfolioNewsConfig(AppConfig):

    default_auto_field = "django.db.models.BigAutoField"

    name = "portfolio_news"

    def ready(self):

        # See config/scheduler_guard.py for exactly why this isn't
        # just an "if RUN_MAIN" check - that alone would mean this
        # scheduler never starts under a production WSGI/ASGI
        # server (waitress/uvicorn/daphne), which don't set
        # RUN_MAIN at all since they have no autoreloader.

        if not should_start_background_schedulers():
            return

        from portfolio_news.services.portfolio_news_scheduler import (
            PortfolioNewsScheduler,
        )

        PortfolioNewsScheduler.start()