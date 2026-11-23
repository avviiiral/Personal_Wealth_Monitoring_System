from mutual_funds.services.mf_pipeline import jobs
from ._mf_pipeline_common import PipelineCommand


class Command(PipelineCommand):
    help = "Find stale/sparse NAV history for active growth schemes and fill it from mfapi.in."

    def add_arguments(self, parser):
        parser.add_argument("--max-requests", type=int, default=None)

    def handle(self, *args, **options):
        self.report(jobs.nav_gap_check(max_requests=options["max_requests"]))
