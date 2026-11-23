from datetime import date

from django.core.management.base import CommandError

from mutual_funds.services.mf_pipeline import jobs
from ._mf_pipeline_common import PipelineCommand


class Command(PipelineCommand):
    help = (
        "Load AMFI 'Disclosure of Risk Parameters' (portfolio/benchmark SD, beta) via amfipy, "
        "then AMFI tracking error for index funds/ETFs."
    )

    def add_arguments(self, parser):
        parser.add_argument("--month", default=None, help="YYYY-MM reporting month, default: previous month.")
        parser.add_argument("--skip-tracking-error", action="store_true")

    def handle(self, *args, **options):
        month = None
        if options["month"]:
            try:
                month = date.fromisoformat(options["month"] + "-01")
            except ValueError as exc:
                raise CommandError("--month must be YYYY-MM.") from exc
        self.report(jobs.sync_amfi_risk(report_month=month))
        if not options["skip_tracking_error"]:
            self.report(jobs.sync_tracking_error())
