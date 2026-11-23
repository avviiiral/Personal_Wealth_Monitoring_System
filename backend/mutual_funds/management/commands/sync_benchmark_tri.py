"""Import daily benchmark total-return-index observations."""
from datetime import date
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from mutual_funds.master_models import MFBenchmarkTRI
from mutual_funds.models import MFSourceRun
from mutual_funds.services.masterdata import finish_source_run, load_source_payload, parse_benchmark_tri_rows, start_source_run

class Command(BaseCommand):
 help="Import daily benchmark TRI history from an official or licensed data file."
 def add_arguments(self,parser):
  source=parser.add_mutually_exclusive_group(); source.add_argument("--from-file"); source.add_argument("--url")
  parser.add_argument("--date",default=timezone.localdate().isoformat()); parser.add_argument("--amc",default="")
  parser.add_argument("--limit",type=int,default=0); parser.add_argument("--dry-run",action="store_true")
  parser.add_argument("--source-name",default="NSE_INDICES_TRI")
 def handle(self,*args,**options):
  try:
   run_date=date.fromisoformat(options["date"]); payload,filename,source_url=load_source_payload(source="benchmark-tri",as_of_date=run_date,from_file=options["from_file"] or "",url=options["url"] or "")
   rows=parse_benchmark_tri_rows(payload,filename=filename)
  except Exception as exc: raise CommandError(str(exc)) from exc
  if options["limit"]>0: rows=rows[:options["limit"]]
  run=None if options["dry_run"] else start_source_run(source=options["source_name"],command="sync_benchmark_tri",as_of_date=run_date,source_url=source_url,raw_file_path=filename if options["from_file"] else "")
  inserted=updated=0
  for row in rows:
   if options["dry_run"]: self.stdout.write(f"{row['index_code']} | {row['date']} | {row['tri_value']}"); inserted+=1; continue
   exists=MFBenchmarkTRI.objects.filter(index_code=row["index_code"],date=row["date"]).exists()
   MFBenchmarkTRI.objects.update_or_create(index_code=row["index_code"],date=row["date"],defaults={"index_name":row["index_name"],"tri_value":row["tri_value"],"source":options["source_name"],"source_url":source_url or "","fetched_at":timezone.now()})
   inserted+=int(not exists); updated+=int(exists)
  self.stdout.write(self.style.SUCCESS(f"Benchmark TRI parsed={len(rows)} inserted={inserted} updated={updated} dry_run={options['dry_run']}"))
  if run: finish_source_run(run,status=MFSourceRun.Status.SUCCESS,parsed=len(rows),inserted=inserted,updated=updated)
