from mutual_funds.services.mf_pipeline import jobs
from ._mf_pipeline_common import PipelineCommand


class Command(PipelineCommand):
    help = "Import benchmark TRI from backend/data/mf_inbox/benchmark_tri/ and configured URLs."

    def handle(self, *args, **options):
        self.report(jobs.sync_benchmark())
