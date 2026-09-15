from django.apps import AppConfig

from config.scheduler_guard import should_start_background_schedulers


class MutualFundsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "mutual_funds"

    def ready(self):
        if not should_start_background_schedulers():
            return

        from .services.underlying_scheduler import MutualFundUnderlyingScheduler

        MutualFundUnderlyingScheduler.start()
