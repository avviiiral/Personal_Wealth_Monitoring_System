from django.apps import AppConfig


class MutualFundsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "mutual_funds"
    # NAV, SIP and underlying-holding jobs live in mutual_funds/jobs.py and
    # are started by the shared background-job framework (market_data/apps.py).
