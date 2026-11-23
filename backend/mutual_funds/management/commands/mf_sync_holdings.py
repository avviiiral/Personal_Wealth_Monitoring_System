from mutual_funds.services.mf_pipeline import holdings_registry, jobs
from ._mf_pipeline_common import PipelineCommand


class Command(PipelineCommand):
    help = "Import AMC portfolio disclosures for AMCs PWMS users hold (inbox files / configured URL templates)."

    def add_arguments(self, parser):
        parser.add_argument("--amc", default=None, choices=sorted(holdings_registry.REGISTRY), help="Limit to one AMC parser.")

    def handle(self, *args, **options):
        self.report(jobs.sync_holdings(amc=options["amc"]))
