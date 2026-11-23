from datetime import datetime

from django.core.management.base import BaseCommand, CommandError

from mutual_funds.services.mf_pipeline import scheduler
from mutual_funds.services.mf_pipeline.runlog import last_success_at


class Command(BaseCommand):
    help = "Run masterdata pipeline jobs now: --job NAME (repeatable), --startup (what backend start would run) or --all."

    def add_arguments(self, parser):
        parser.add_argument("--job", action="append", default=[], choices=list(scheduler.STARTUP_ORDER))
        parser.add_argument("--startup", action="store_true", help="Run the startup plan (respects cooldowns).")
        parser.add_argument("--all", action="store_true", help="Run every job in dependency order.")
        parser.add_argument("--dry-run", action="store_true", help="Only print which jobs would run.")

    def handle(self, *args, **options):
        if options["all"]:
            names = list(scheduler.STARTUP_ORDER)
        elif options["startup"]:
            names = scheduler.startup_plan(now=datetime.now(scheduler.IST), last_success=last_success_at)
        else:
            names = options["job"]
        if not names:
            raise CommandError("Nothing to run: pass --job, --startup or --all.")
        self.stdout.write("Jobs: " + ", ".join(names))
        if options["dry_run"]:
            return
        for name in names:
            for run in scheduler.execute(name):
                reason = (run.metadata or {}).get("skip_reason", "")
                self.stdout.write(f"{run.command}: {run.status} {reason} ins={run.rows_inserted} upd={run.rows_updated} failed={run.rows_failed}")
