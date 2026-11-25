from django.apps import AppConfig


class MfIntelligenceConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "mf_intelligence"
    verbose_name = "Mutual Fund Intelligence"
    # The nightly sync is the ``mf_intelligence`` job (mf_intelligence/jobs.py),
    # started by the shared background-job framework in market_data/apps.py.
