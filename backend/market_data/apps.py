from django.apps import AppConfig

from config.scheduler_guard import should_start_background_schedulers


class MarketDataConfig(AppConfig):

    default_auto_field = "django.db.models.BigAutoField"

    name = "market_data"

    def ready(self):

        # See config/scheduler_guard.py for exactly why this isn't
        # just an "if RUN_MAIN" check - that alone would mean these
        # jobs never start under a production WSGI/ASGI server
        # (waitress/uvicorn/daphne), which don't set RUN_MAIN at all
        # since they have no autoreloader.

        if not should_start_background_schedulers():
            return

        # One framework runs every background data job (prices, NAVs, SIPs,
        # MF Intelligence, Watch List, Security Master, security metrics,
        # MF underlying, corporate actions). Jobs from other apps are
        # registered in config/scheduling/registry.py. The portfolio news
        # loop keeps its own thread (portfolio_news/apps.py).
        from config.scheduling.framework import start_background_jobs

        start_background_jobs()
