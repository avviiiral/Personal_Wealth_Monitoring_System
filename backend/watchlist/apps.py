from django.apps import AppConfig


class WatchlistConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "watchlist"
    # The nightly Watch List refresh is the ``watchlist`` job
    # (watchlist/jobs.py), run right after the MF Intelligence sync by the
    # shared background-job framework. It previously used its own RUN_MAIN
    # check and therefore never started under waitress or uvicorn.
