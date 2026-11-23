from mutual_funds.services.mf_pipeline import jobs
from ._mf_pipeline_common import PipelineCommand


class Command(PipelineCommand):
    help = (
        "One-time historical NAV backfill into AMFIMasterNAV (missing dates only). "
        "--source archive reads an extracted captn3m0/historical-mf-data funds.db; "
        "--source mfapi calls api.mfapi.in per scheme (rate limited)."
    )

    def add_arguments(self, parser):
        parser.add_argument("--source", choices=("mfapi", "archive"), default="mfapi")
        parser.add_argument("--db-path", default="", help="Path to extracted funds.db (archive source).")
        parser.add_argument("--scope", choices=("held", "growth", "all"), default="held")
        parser.add_argument("--scheme-code", action="append", default=[])
        parser.add_argument("--max-requests", type=int, default=None)

    def handle(self, *args, **options):
        if options["source"] == "archive":
            run = jobs.backfill_nav_from_archive(options["db_path"], scheme_codes=options["scheme_code"] or None)
        elif options["scheme_code"]:
            run = jobs.fill_nav_from_mfapi(scheme_codes=options["scheme_code"], max_requests=options["max_requests"])
        else:
            run = jobs.backfill_nav(source="mfapi", scope=options["scope"], max_requests=options["max_requests"])
        self.report(run)
