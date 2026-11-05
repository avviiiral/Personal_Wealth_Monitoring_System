from django.apps import AppConfig


class CorporateActionsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "corporate_actions"

    def ready(self):
        from .automation import start_daily_scheduler

        start_daily_scheduler()
