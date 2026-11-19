from django.core.management.base import BaseCommand, CommandError

from mutual_funds.services.sebi_mf_analytics import (
    DEFAULT_REQUESTS_PER_SECOND,
    DEFAULT_RISK_FREE_RATE,
    build_sebi_universe_analytics,
)


class Command(BaseCommand):
    help = (
        "Build trailing/rolling NAV performance, risk, SEBI peer rankings, "
        "category summaries and the SEBI_Universe_MF_Analytics.xlsx workbook."
    )

    def add_arguments(self, parser):
        parser.add_argument("--plans", choices=("direct", "both"), default="direct")
        parser.add_argument("--categories", nargs="*", default=None)
        parser.add_argument("--max-schemes", type=int, default=None)
        parser.add_argument("--workers", type=int, default=4)
        parser.add_argument(
            "--requests-per-second",
            type=float,
            default=DEFAULT_REQUESTS_PER_SECOND,
        )
        parser.add_argument("--refresh-cache", action="store_true")
        parser.add_argument("--retries", type=int, default=3)
        parser.add_argument(
            "--risk-free-rate",
            type=float,
            default=DEFAULT_RISK_FREE_RATE * 100.0,
            help="Annual risk-free rate in percent. Defaults to 6.5.",
        )
        parser.add_argument("--expense-ratio-file", default=None)
        parser.add_argument("--category-flow-file", default=None)
        parser.add_argument("--output", default=None)

    def handle(self, *args, **options):
        try:
            result = build_sebi_universe_analytics(
                plans=options["plans"],
                categories=options["categories"],
                max_schemes=options["max_schemes"],
                workers=options["workers"],
                requests_per_second=options["requests_per_second"],
                refresh_cache=options["refresh_cache"],
                retries=options["retries"],
                risk_free_rate=max(0.0, options["risk_free_rate"]) / 100.0,
                expense_ratio_file=options["expense_ratio_file"],
                category_flow_file=options["category_flow_file"],
                output_path=options["output"],
            )
        except Exception as exc:
            raise CommandError(f"SEBI mutual-fund analytics build failed: {exc}") from exc

        self.stdout.write(self.style.SUCCESS("SEBI mutual-fund analytics build completed."))
        for label, key in (
            ("Plan mode", "plans_processed"),
            ("Schemes requested", "schemes_requested"),
            ("Funds with usable history", "funds_with_history"),
            ("Funds without usable history", "funds_without_history"),
            ("Local MFapi cache hits", "mfapi_cache_hits"),
            ("Current SEBI leaf categories", "current_sebi_leaf_categories"),
            ("Portfolio overlap pairs", "portfolio_overlap_pairs"),
        ):
            self.stdout.write(f"{label}: {result[key]}")
        self.stdout.write(f"Workbook: {result['workbook_path']}")
        self.stdout.write(f"Category return/risk chart: {result['category_risk_chart']}")
        self.stdout.write(f"Top Q1 chart: {result['top_q1_chart']}")
        self.stdout.write(
            "No IndianAPI request was made. MFapi JSON histories are cached locally; "
            "subsequent builds use cache unless --refresh-cache is passed."
        )
