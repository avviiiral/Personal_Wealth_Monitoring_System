"""Import dated AMC/AMFI TER disclosures."""
from datetime import date
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from mutual_funds.models import AMFIMasterScheme, MFSourceRun
from mutual_funds.services.masterdata import finish_source_run, load_source_payload, parse_ter_rows, resolve_master_scheme, resolve_source_url, start_source_run, upsert_masterdata_snapshot

class Command(BaseCommand):
    help = "Import dated Direct and Regular TER values."
    def add_arguments(self, parser):
        source=parser.add_mutually_exclusive_group(); source.add_argument("--from-file"); source.add_argument("--url")
        parser.add_argument("--date", default=timezone.localdate().isoformat()); parser.add_argument("--amc", default="")
        parser.add_argument("--source-url", default="", help="Official disclosure URL when --from-file is used.")
        parser.add_argument("--limit", type=int, default=0); parser.add_argument("--dry-run", action="store_true")
    def handle(self, *args, **options):
        try:
            as_of=date.fromisoformat(options["date"])
            payload, filename, downloaded_url=load_source_payload(source="ter",as_of_date=as_of,from_file=options["from_file"] or "",url=options["url"] or "")
            source_url=resolve_source_url(from_file=options["from_file"] or "",supplied_source_url=options["source_url"],downloaded_url=downloaded_url)
            rows=parse_ter_rows(payload,filename=filename,as_of_date=as_of)
        except Exception as exc: raise CommandError(str(exc)) from exc
        if options["limit"]>0: rows=rows[:options["limit"]]
        run=None if options["dry_run"] else start_source_run(source="AMFI_TER",command="sync_mf_ter",as_of_date=as_of,source_url=source_url,raw_file_path=options["from_file"] or filename)
        inserted=updated=unmatched=skipped=0; errors=[]
        for row in rows:
            if row["scheme_code"]:
                scheme=AMFIMasterScheme.objects.filter(scheme_code=row["scheme_code"],is_active=True).first()
                status="scheme_code" if scheme else "unmatched"
            else: scheme,status=resolve_master_scheme(isin=row["isin"],scheme_name=row["scheme_name"])
            if not scheme:
                unmatched+=1
                if len(errors)<20: errors.append({"scheme":row["scheme_name"],"status":status})
                continue
            if options["amc"] and options["amc"].casefold() not in (scheme.amc_name or "").casefold(): skipped+=1; continue
            plan=(row["plan"] or scheme.plan or "").casefold()
            field="ter_direct_pct" if "direct" in plan else "ter_regular_pct" if "regular" in plan else ""
            if not field:
                unmatched+=1; errors.append({"scheme":scheme.scheme_name,"status":"plan_missing"}); continue
            if options["dry_run"]:
                self.stdout.write(f"{scheme.scheme_code} | {field}={row['ter_pct']}"); inserted+=1; continue
            _,created=upsert_masterdata_snapshot(scheme,as_of_date=as_of,values={field:row["ter_pct"],"ter_as_of":as_of},source="AMFI_TER",source_url=source_url,field_names=[field,"ter_as_of"])
            inserted+=int(created); updated+=int(not created)
        self.stdout.write(self.style.SUCCESS(f"TER parsed={len(rows)} matched={inserted+updated} unmatched={unmatched} skipped={skipped} dry_run={options['dry_run']}"))
        if run: finish_source_run(run,status=MFSourceRun.Status.PARTIAL if unmatched else MFSourceRun.Status.SUCCESS,parsed=len(rows),inserted=inserted,updated=updated,skipped=skipped,unmatched=unmatched,errors=errors)
