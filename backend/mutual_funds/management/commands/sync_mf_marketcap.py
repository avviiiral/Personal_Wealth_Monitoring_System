"""Import AMFI six-month ISIN-level large/mid/small-cap classifications."""
from datetime import date
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from mutual_funds.master_models import MFMarketCapClassification
from mutual_funds.models import MFSourceRun
from mutual_funds.services.masterdata import finish_source_run, load_source_payload, parse_market_cap_rows, resolve_source_url, start_source_run

class Command(BaseCommand):
 help="Import the official AMFI six-month large/mid/small-cap ISIN classification."
 def add_arguments(self,parser):
  source=parser.add_mutually_exclusive_group(); source.add_argument("--from-file"); source.add_argument("--url")
  parser.add_argument("--date",default=timezone.localdate().isoformat()); parser.add_argument("--effective-period",required=True)
  parser.add_argument("--source-url",default="",help="Official AMFI document URL when --from-file is used.")
  parser.add_argument("--amc",default=""); parser.add_argument("--limit",type=int,default=0); parser.add_argument("--dry-run",action="store_true")
 def handle(self,*args,**options):
  try:
   as_of=date.fromisoformat(options["date"]); payload,filename,downloaded_url=load_source_payload(source="market-cap",as_of_date=as_of,from_file=options["from_file"] or "",url=options["url"] or "")
   source_url=resolve_source_url(from_file=options["from_file"] or "",supplied_source_url=options["source_url"],downloaded_url=downloaded_url)
   rows=parse_market_cap_rows(payload,filename=filename,effective_period=options["effective_period"])
  except Exception as exc: raise CommandError(str(exc)) from exc
  if options["limit"]>0: rows=rows[:options["limit"]]
  run=None if options["dry_run"] else start_source_run(source="AMFI_MARKET_CAP",command="sync_mf_marketcap",as_of_date=as_of,source_url=source_url,raw_file_path=options["from_file"] or filename)
  inserted=updated=0
  for row in rows:
   if options["dry_run"]: self.stdout.write(f"{row['isin']} | {row['market_cap_class']}"); inserted+=1; continue
   exists=MFMarketCapClassification.objects.filter(isin=row["isin"],effective_period=options["effective_period"]).exists()
   MFMarketCapClassification.objects.update_or_create(isin=row["isin"],effective_period=options["effective_period"],defaults={"company_name":row["company_name"],"market_cap_rank":row["market_cap_rank"],"market_cap_class":row["market_cap_class"],"source":"AMFI","source_url":source_url or "","fetched_at":timezone.now()})
   inserted+=int(not exists); updated+=int(exists)
  self.stdout.write(self.style.SUCCESS(f"AMFI cap parsed={len(rows)} inserted={inserted} updated={updated} dry_run={options['dry_run']}"))
  if run: finish_source_run(run,status=MFSourceRun.Status.SUCCESS,parsed=len(rows),inserted=inserted,updated=updated)
