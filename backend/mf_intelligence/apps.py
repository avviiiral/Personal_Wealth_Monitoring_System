from django.apps import AppConfig

from config.scheduler_guard import should_start_background_schedulers


class MfIntelligenceConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "mf_intelligence"
    verbose_name = "Mutual Fund Intelligence"

    def ready(self):
        if not should_start_background_schedulers():
            return

        from mf_intelligence.services.scheduler import MFIntelligenceScheduler

        MFIntelligenceScheduler.start()
