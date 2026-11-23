"""Import market-wide portfolio holdings for five large AMCs."""
from datetime import date
from django.core.management.base import BaseCommand, CommandError
from mutual_funds.models import AMFIMasterScheme, MFSourceRun
from mutual_funds.services.masterdata import finish_source_run, load_source_payload, parse_portfolio_rows, persist_portfolio_rows, resolve_master_scheme, resolve_source_url, start_source_run

AMC_ADAPTERS={
 "hdfc":{"canonical":"HDFC Mutual Fund","aliases":("hdfc mutual fund","hdfc asset management")},
 "icici_prudential":{"canonical":"ICICI Prudential Mutual Fund","aliases":("icici prudential mutual fund","icici prudential amc")},
 "sbi":{"canonical":"SBI Mutual Fund","aliases":("sbi mutual fund","sbi funds management")},
 "nippon_india":{"canonical":"Nippon India Mutual Fund","aliases":("nippon india mutual fund","reliance mutual fund")},
 "uti":{"canonical":"UTI Mutual Fund","aliases":("uti mutual fund","uti asset management")},
}
def resolve_amc(value):
 key=(value or "").casefold().replace("-","_").replace(" ","_")
 if key in AMC_ADAPTERS: return key,AMC_ADAPTERS[key]["canonical"]
 for adapter,data in AMC_ADAPTERS.items():
  if any(alias in (value or "").casefold() for alias in data["aliases"]): return adapter,data["canonical"]
 raise ValueError("Unsupported --amc. Choose: "+", ".join(AMC_ADAPTERS))

class Command(BaseCommand):
 help="Import a dated global mutual-fund portfolio disclosure."
 def add_arguments(self,parser):
  source=parser.add_mutually_exclusive_group(); source.add_argument("--from-file"); source.add_argument("--url")
  parser.add_argument("--date",required=True); parser.add_argument("--amc",required=True)
  parser.add_argument("--scheme-code",default=""); parser.add_argument("--limit",type=int,default=0); parser.add_argument("--dry-run",action="store_true")
  parser.add_argument("--source-url",default="",help="Official disclosure URL when --from-file is used.")
 def handle(self,*args,**options):
  try:
   portfolio_date=date.fromisoformat(options["date"]); adapter,canonical=resolve_amc(options["amc"])
   payload,filename,downloaded_url=load_source_payload(source=f"portfolios/{adapter}",as_of_date=portfolio_date,from_file=options["from_file"] or "",url=options["url"] or "")
   source_url=resolve_source_url(from_file=options["from_file"] or "",supplied_source_url=options["source_url"],downloaded_url=downloaded_url)
   rows=parse_portfolio_rows(payload,filename=filename,amc=canonical,portfolio_date=portfolio_date)
  except Exception as exc: raise CommandError(str(exc)) from exc
  if options["limit"]>0: rows=rows[:options["limit"]]
  pinned=AMFIMasterScheme.objects.filter(scheme_code=options["scheme_code"],is_active=True).first() if options["scheme_code"] else None
  if options["scheme_code"] and not pinned: raise CommandError(f"Unknown active scheme code: {options['scheme_code']}")
  run=None if options["dry_run"] else start_source_run(source=f"AMC_PORTFOLIO_{adapter.upper()}",command="sync_mf_portfolios",as_of_date=portfolio_date,source_url=source_url,raw_file_path=options["from_file"] or filename)
  grouped={}; unmatched=skipped=inserted=updated=0; errors=[]
  for row in rows:
   if pinned:
    scheme,status=pinned,"scheme_code"
   elif row.get("scheme_code"):
    scheme=AMFIMasterScheme.objects.filter(scheme_code=row["scheme_code"],is_active=True).first()
    status="scheme_code" if scheme else "unmatched"
   else:
    scheme,status=resolve_master_scheme(isin=row.get("scheme_isin") or "",scheme_name=row.get("scheme_name") or "")
   if not scheme:
    unmatched+=1
    if len(errors)<20: errors.append({"scheme":row.get("scheme_name",""),"status":status})
    continue
   if canonical.casefold() not in (scheme.amc_name or "").casefold():
    skipped+=1
    if len(errors)<20: errors.append({"scheme":scheme.scheme_name,"status":"amc_mismatch"})
    continue
   grouped.setdefault(scheme,[]).append(row)
  for scheme,holding_rows in grouped.items():
   if options["dry_run"]:
    self.stdout.write(f"{scheme.scheme_code} | {scheme.scheme_name} | holdings={len(holding_rows)}"); inserted+=len(holding_rows); continue
   result=persist_portfolio_rows(scheme,holding_rows,portfolio_date=portfolio_date,source=f"AMC_PORTFOLIO_{adapter.upper()}",source_url=source_url)
   inserted+=result["inserted"]; updated+=result["updated"]; skipped+=result["skipped"]
  self.stdout.write(self.style.SUCCESS(f"Portfolio parsed={len(rows)} schemes={len(grouped)} inserted={inserted} updated={updated} unmatched={unmatched} skipped={skipped} dry_run={options['dry_run']}"))
  if run: finish_source_run(run,status=MFSourceRun.Status.PARTIAL if unmatched else MFSourceRun.Status.SUCCESS,parsed=len(rows),inserted=inserted,updated=updated,skipped=skipped,unmatched=unmatched,errors=errors,metadata={"amc":canonical,"adapter":adapter})
