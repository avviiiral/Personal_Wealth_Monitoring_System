from mutual_funds.services.mf_pipeline import jobs
from ._mf_pipeline_common import PipelineCommand


class Command(PipelineCommand):
    help = "Download AMFI NAVAll.txt; upsert scheme master + latest NAV; mark schemes missing >7 days inactive."

    def handle(self, *args, **options):
        self.report(jobs.sync_nav())
