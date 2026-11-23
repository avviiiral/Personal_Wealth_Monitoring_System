from mutual_funds.services.mf_pipeline import jobs
from ._mf_pipeline_common import PipelineCommand


class Command(PipelineCommand):
    help = "Load AMFI TER (Regular + Direct Total TER) via amfipy into mf_masterdata."

    def add_arguments(self, parser):
        parser.add_argument("--month", default=None, help="MM-YYYY, default: latest month AMFI lists.")

    def handle(self, *args, **options):
        self.report(jobs.sync_ter(month=options["month"]))
