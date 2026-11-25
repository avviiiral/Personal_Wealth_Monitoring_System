"""Run one scheduled background job on demand, or list their state.

    python manage.py run_scheduled_job --list
    python manage.py run_scheduled_job mf_nav
    python manage.py run_scheduled_job security_master --check
    python manage.py run_scheduled_job market_prices --if-due

By default the job runs even if it is current (that is the point of a manual
run). --if-due applies the same is_current() rule the scheduler uses. The
result is recorded in data/scheduler_state.json like a scheduled run.
"""
from django.core.management.base import BaseCommand, CommandError

from config.scheduling import clock
from config.scheduling.framework import build_runner
from config.scheduling.registry import build_jobs


class Command(BaseCommand):
    help = "Run one PWMS background job now (see --list for names)."

    def add_arguments(self, parser):
        parser.add_argument("name", nargs="?", help="Job name, e.g. mf_nav, market_prices, security_master.")
        parser.add_argument("--list", action="store_true", help="List jobs with schedule and last success.")
        parser.add_argument("--check", action="store_true", help="Only report whether the job is current.")
        parser.add_argument("--if-due", action="store_true", help="Run only if the job is not current.")

    def handle(self, *args, **options):
        jobs = {job.name: job for job in build_jobs()}
        runner = build_runner()
        now = clock.now()

        if options["list"] or not options["name"]:
            self.stdout.write(f"State file: {runner.store.path}")
            for job in jobs.values():
                entry = runner.store.get(job.name)
                self.stdout.write(
                    f"  {job.describe():<45} enabled={job.enabled!s:<5} "
                    f"last_success={entry.get('last_success') or '-'} last_status={entry.get('last_status') or '-'}"
                )
            return

        name = options["name"]
        if name not in jobs:
            raise CommandError(f"Unknown job {name!r}. Valid names: {', '.join(jobs)}")
        job = jobs[name]

        if options["check"]:
            current, reason = job.is_current(now, last_success=runner.store.last_success(name), at_startup=True)
            self.stdout.write(f"{name}: {'current' if current else 'due'} ({reason})")
            return

        outcome = runner.execute(job, now=now, reason="manual", force=not options["if_due"])
        counts = " ".join(f"{key}={value}" for key, value in (outcome.counts or {}).items())
        line = f"{name}: status={outcome.status} duration={outcome.duration:.1f}s {counts}".rstrip()
        if outcome.error:
            line += f" error={outcome.error}"
        writer = self.stdout if outcome.status in ("success", "current") else self.stderr
        writer.write(line)
