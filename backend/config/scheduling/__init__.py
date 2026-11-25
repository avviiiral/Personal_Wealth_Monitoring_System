"""PWMS background-job framework.

One small framework replaces the separate scheduler threads that each app used
to start. Every job declares:

* a name,
* an ``is_current()`` check (based on stored data where possible),
* a schedule (an interval, a daily time, or "right after another job"),
* a ``run()`` function.

Two worker threads run the jobs: one for interval jobs (the 15-minute price
refresh) and one for nightly jobs, which run one after another and also
perform the startup catch-up. The portfolio news loop keeps its own thread.

See docs/operations/BACKGROUND_JOBS.md for the schedule and config keys.
"""
