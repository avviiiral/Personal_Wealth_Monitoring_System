from django.core.management.base import BaseCommand
from filing_intelligence.services.pipeline import ingest_exchange_filings

class Command(BaseCommand):
    help="Ingest NSE/BSE exchange filings and generate portfolio/watchlist alerts."
    def add_arguments(self,parser):
        parser.add_argument("--exchange",action="append",choices=["nse","bse"])
        parser.add_argument("--hours",type=int,default=24)
        parser.add_argument("--dry-run",action="store_true")
    def handle(self,*args,**options):
        exchanges=[x.upper() for x in (options.get("exchange") or ["nse","bse"])]
        stats=ingest_exchange_filings(hours=options["hours"],exchanges=exchanges,dry_run=options["dry_run"])
        self.stdout.write(self.style.SUCCESS("Exchange filing ingestion complete."))
        for k,v in stats.items(): self.stdout.write(f"  {k}: {v}")
