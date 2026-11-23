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

        # Automated mf_masterdata ingestion: runs every job once at startup
        # (subject to per-job cooldowns), then follows its IST timeline.
        # Disable with PWMS_MF_PIPELINE_ENABLED=0.
        from .services.mf_pipeline.scheduler import MFPipelineScheduler

        MFPipelineScheduler.start()
