from django.apps import AppConfig


class CorporateActionsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "corporate_actions"
    # The nightly NSE scan is the ``corporate_actions`` job
    # (corporate_actions/jobs.py), started by the shared background-job
    # framework in market_data/apps.py.
