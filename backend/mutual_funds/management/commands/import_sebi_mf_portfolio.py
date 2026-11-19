from datetime import datetime

from django.core.management.base import BaseCommand, CommandError

from mutual_funds.services.sebi_mf_analytics import import_monthly_portfolio_holdings_xlsx


class Command(BaseCommand):
    help = "Import one AMC monthly portfolio disclosure workbook for pairwise ISIN overlap."

    def add_arguments(self, parser):
        parser.add_argument("--file", required=True)
        parser.add_argument("--scheme-code", required=True)
        parser.add_argument(
            "--portfolio-date",
            default=None,
            help="Disclosure date in YYYY-MM-DD. Defaults to the file's modified date.",
        )

    def handle(self, *args, **options):
        portfolio_date = None
        if options["portfolio_date"]:
            try:
                portfolio_date = datetime.strptime(options["portfolio_date"], "%Y-%m-%d").date()
            except ValueError as exc:
                raise CommandError("--portfolio-date must use YYYY-MM-DD.") from exc

        try:
            result = import_monthly_portfolio_holdings_xlsx(
                options["file"],
                scheme_code=options["scheme_code"],
                portfolio_date=portfolio_date,
            )
        except Exception as exc:
            raise CommandError(f"Monthly mutual-fund portfolio import failed: {exc}") from exc

        self.stdout.write(self.style.SUCCESS("Monthly portfolio disclosure imported."))
        self.stdout.write(f"Scheme: {result['scheme_name']} ({result['scheme_code']})")
        self.stdout.write(f"Disclosure date: {result['portfolio_date']}")
        self.stdout.write(f"ISIN holdings: {result['holdings']}")
        self.stdout.write(f"Total disclosed weight: {result['total_weight_pct']}%")
        self.stdout.write(f"Local cache: {result['cache_directory']}")
