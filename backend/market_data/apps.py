from django.apps import AppConfig

from config.scheduler_guard import should_start_background_schedulers


class MarketDataConfig(AppConfig):

    default_auto_field = "django.db.models.BigAutoField"

    name = "market_data"

    def ready(self):

        # See config/scheduler_guard.py for exactly why this isn't
        # just an "if RUN_MAIN" check - that alone would mean these
        # schedulers never start under a production WSGI/ASGI
        # server (waitress/uvicorn/daphne), which don't set
        # RUN_MAIN at all since they have no autoreloader.

        if not should_start_background_schedulers():
            return

        from market_data.services.market_price_scheduler import (
            MarketPriceScheduler,
        )

        MarketPriceScheduler.start()

        from market_data.services.daily_refresh_scheduler import (
            DailyRefreshScheduler,
        )

        DailyRefreshScheduler.start()