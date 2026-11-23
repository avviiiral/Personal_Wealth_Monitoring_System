from datetime import date

from django.core.management.base import CommandError

from mutual_funds.services.mf_pipeline import jobs
from ._mf_pipeline_common import PipelineCommand


class Command(PipelineCommand):
    help = "Load the 91-day T-bill (risk-free) rate from inbox files / PWMS_MF_RISK_FREE_URL, or set it manually."

    def add_arguments(self, parser):
        parser.add_argument("--rate", type=float, default=None, help="Annual % e.g. 6.85 (manual entry).")
        parser.add_argument("--date", default=None, help="YYYY-MM-DD for --rate (auction/as-of date).")

    def handle(self, *args, **options):
        if options["rate"] is not None:
            if not options["date"]:
                raise CommandError("--date is required with --rate.")
            if not 0 < options["rate"] < 100:
                raise CommandError("--rate must be an annual percentage between 0 and 100.")
            row = jobs.set_risk_free_rate(date.fromisoformat(options["date"]), options["rate"])
            self.stdout.write(self.style.SUCCESS(f"Stored 91D risk-free rate {row.rate_pct}% for {row.date}."))
            return
        self.report(jobs.sync_risk_free())
