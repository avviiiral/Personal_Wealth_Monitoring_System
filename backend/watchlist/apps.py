import os

from django.apps import AppConfig


class WatchlistConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "watchlist"

    def ready(self):
        if os.environ.get("PWMS_DISABLE_BACKGROUND_SCHEDULERS") == "1":
            return
        from watchlist.services.scheduler import WatchListScheduler
        WatchListScheduler.start()
