"""
Decides whether the current process should start PWMS's in-process
background schedulers (MarketPriceScheduler, DailyRefreshScheduler,
PortfolioNewsScheduler) - shared by every app's AppConfig.ready()
that starts one, so the same rule applies everywhere instead of
being copy-pasted and drifting.

The rule: start when actually serving requests, whichever way
that's being done - `manage.py runserver`, or a production WSGI/
ASGI server (waitress, uvicorn, daphne, gunicorn) loading
config.wsgi/config.asgi directly. Never start for a one-off
management command (test, migrate, shell, makemigrations, etc.).

Why this needs care: Django's `runserver` autoreloader starts the
whole app TWICE (a watcher process, then the real worker process),
distinguished by the RUN_MAIN environment variable - so a naive
"start on ready()" would start every scheduler twice under
runserver. But RUN_MAIN is a runserver-specific autoreloader
signal; a WSGI/ASGI server never sets it at all, and never goes
through manage.py's argv either. So the two conditions below are
both necessary, and checking only one of them breaks the other
deployment method:
    - manage.py test/migrate/shell/etc. -> never start
    - manage.py runserver -> start only in the RUN_MAIN=="true" process
    - anything else (waitress/uvicorn/daphne loading config.wsgi or
      config.asgi directly - argv[0] won't be manage.py at all) ->
      start unconditionally, since there's no autoreloader to double
      it and no one-off command to suppress it for
"""

import os
import sys


def should_start_background_schedulers():

    argv = sys.argv

    is_manage_py = bool(argv) and argv[0].endswith("manage.py")

    if is_manage_py:

        subcommand = argv[1] if len(argv) > 1 else None

        if subcommand != "runserver":
            # test, migrate, shell, makemigrations, dbshell,
            # createsuperuser, or anything else - never start a
            # background scheduler for a one-off command.
            return False

        # manage.py runserver specifically - only the real worker
        # process (not the autoreloader's watcher process) should
        # start it.
        return os.environ.get("RUN_MAIN") == "true"

    # Not manage.py at all - a WSGI/ASGI server (waitress, uvicorn,
    # daphne, gunicorn) loading config.wsgi/config.asgi directly.
    # No autoreloader in play, so no double-start risk; start.
    return True
