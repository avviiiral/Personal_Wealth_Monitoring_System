from datetime import date

from mutual_funds.services.mf_pipeline import jobs
from ._mf_pipeline_common import PipelineCommand


class Command(PipelineCommand):
    help = "Build today's mf_masterdata snapshot and add calculated 1Y/3Y ratios + latest NAV."

    def add_arguments(self, parser):
        parser.add_argument("--as-of", default=None, help="YYYY-MM-DD, default: latest NAVAll date.")
        parser.add_argument("--scope", choices=("growth", "held", "all"), default=None)
        parser.add_argument("--scheme-code", action="append", default=[])

    def handle(self, *args, **options):
        as_of = date.fromisoformat(options["as_of"]) if options["as_of"] else None
        self.report(jobs.calc_ratios(as_of=as_of, scope=options["scope"], scheme_codes=options["scheme_code"] or None))
