"""Import dated scheme info and factsheet attributes."""
from datetime import date
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from mutual_funds.models import AMFIMasterScheme, MFSourceRun
from mutual_funds.services.masterdata import finish_source_run, load_source_payload, parse_scheme_info_rows, resolve_master_scheme, start_source_run, upsert_masterdata_snapshot

FIELDS=("benchmark_name","inception_date","fund_managers","riskometer","benchmark_riskometer","exit_load_text","min_lumpsum","min_sip","pe","pb","dividend_yield_pct","turnover_ratio","ytm_pct","modified_duration","macaulay_duration","average_maturity")
class Command(BaseCommand):
 help="Import official scheme information and factsheet attributes."
 def add_arguments(self,parser):
  source=parser.add_mutually_exclusive_group(); source.add_argument("--from-file"); source.add_argument("--url")
  parser.add_argument("--date",default=timezone.localdate().isoformat()); parser.add_argument("--amc",default="")
  parser.add_argument("--limit",type=int,default=0); parser.add_argument("--dry-run",action="store_true")
 def handle(self,*args,**options):
  try:
   as_of=date.fromisoformat(options["date"]); payload,filename,source_url=load_source_payload(source="scheme-info",as_of_date=as_of,from_file=options["from_file"] or "",url=options["url"] or "")
   rows=parse_scheme_info_rows(payload,filename=filename,as_of_date=as_of)
  except Exception as exc: raise CommandError(str(exc)) from exc
  if options["limit"]>0: rows=rows[:options["limit"]]
  run=None if options["dry_run"] else start_source_run(source="AMC_SCHEME_INFO",command="sync_mf_scheme_info",as_of_date=as_of,source_url=source_url,raw_file_path=filename if options["from_file"] else "")
  inserted=updated=unmatched=skipped=0; errors=[]
  for row in rows:
   if row["scheme_code"]:
    scheme=AMFIMasterScheme.objects.filter(scheme_code=row["scheme_code"],is_active=True).first(); status="scheme_code" if scheme else "unmatched"
   else: scheme,status=resolve_master_scheme(isin=row["isin"],scheme_name=row["scheme_name"])
   if not scheme:
    unmatched+=1
    if len(errors)<20: errors.append({"scheme":row["scheme_name"],"status":status})
    continue
   if options["amc"] and options["amc"].casefold() not in (scheme.amc_name or "").casefold(): skipped+=1; continue
   values={key:row[key] for key in FIELDS if row.get(key) is not None}
   if not values: skipped+=1; continue
   if options["dry_run"]: self.stdout.write(f"{scheme.scheme_code} | fields={','.join(values)}"); inserted+=1; continue
   _,created=upsert_masterdata_snapshot(scheme,as_of_date=as_of,values=values,source="AMC_SCHEME_INFO",source_url=source_url,field_names=list(values))
   inserted+=int(created); updated+=int(not created)
  self.stdout.write(self.style.SUCCESS(f"Scheme-info parsed={len(rows)} matched={inserted+updated} unmatched={unmatched} skipped={skipped} dry_run={options['dry_run']}"))
  if run: finish_source_run(run,status=MFSourceRun.Status.PARTIAL if unmatched else MFSourceRun.Status.SUCCESS,parsed=len(rows),inserted=inserted,updated=updated,skipped=skipped,unmatched=unmatched,errors=errors)
