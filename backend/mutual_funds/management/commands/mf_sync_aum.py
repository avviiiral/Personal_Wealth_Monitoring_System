from mutual_funds.services.mf_pipeline import jobs
from ._mf_pipeline_common import PipelineCommand


class Command(PipelineCommand):
    help = "Load the newest AMFI scheme-wise average AUM quarter (via amfipy, keyed by AMFI code)."

    def handle(self, *args, **options):
        self.report(jobs.sync_aum())
