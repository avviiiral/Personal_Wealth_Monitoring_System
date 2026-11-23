from datetime import datetime

from django.core.management.base import BaseCommand

from mutual_funds.master_models import MFSourceRun
from mutual_funds.services.mf_pipeline.scheduler import IST, JOB_SPECS, STARTUP_ORDER, next_slot, pipeline_enabled

JOB_COMMANDS = {"mf_sync_risk": ("mf_sync_risk", "mf_sync_tracking_error")}


class Command(BaseCommand):
    help = "Show last run and next scheduled run (IST) for each masterdata pipeline job."

    def handle(self, *args, **options):
        now = datetime.now(IST)
        self.stdout.write(f"Pipeline scheduler enabled: {pipeline_enabled()}  (now {now:%Y-%m-%d %H:%M} IST)")
        for name in STARTUP_ORDER:
            spec = JOB_SPECS[name]
            upcoming = next_slot(spec, now)
            self.stdout.write(f"\n{name} - {spec.description}")
            self.stdout.write(f"  next: {upcoming:%a %Y-%m-%d %H:%M} IST" if upcoming else "  next: (startup / manual only)")
            for command in JOB_COMMANDS.get(name, (name,)):
                run = MFSourceRun.objects.filter(command=command).order_by("-started_at").first()
                if run is None:
                    self.stdout.write(f"  last {command}: never")
                    continue
                reason = (run.metadata or {}).get("skip_reason", "")
                self.stdout.write(
                    f"  last {command}: {run.status} {reason} at {run.started_at.astimezone(IST):%Y-%m-%d %H:%M} "
                    f"ins={run.rows_inserted} upd={run.rows_updated} unmatched={run.rows_unmatched} failed={run.rows_failed}"
                )
