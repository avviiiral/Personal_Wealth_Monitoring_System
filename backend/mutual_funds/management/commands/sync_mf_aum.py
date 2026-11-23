"""Import quarterly AMFI AAUM and explicitly dated AMC month-end AUM."""
from calendar import monthrange
from datetime import date
from decimal import Decimal
from django.core.management.base import BaseCommand, CommandError
from mutual_funds.models import AMFIMasterScheme, AMFISchemeAAUM, MFSourceRun
from mutual_funds.services.masterdata import finish_source_run, load_source_payload, parse_aum_rows, resolve_master_scheme, resolve_source_url, start_source_run, upsert_masterdata_snapshot

def quarter_end(start):
    month = ((start.month - 1) // 3 + 1) * 3
    return date(start.year, month, monthrange(start.year, month)[1])

def quarter_label(start):
    return (("January - March", "April - June", "July - September", "October - December")[(start.month - 1) // 3]) + f" {start.year}"

class Command(BaseCommand):
    help = "Import AMFI quarterly AAUM or AMC month-end scheme AUM."
    def add_arguments(self, parser):
        source = parser.add_mutually_exclusive_group(required=True)
        source.add_argument("--from-file"); source.add_argument("--url")
        parser.add_argument("--date", required=True, help="Quarter start for QUARTERLY_AVG; exact month-end for MONTH_END.")
        parser.add_argument("--aum-type", choices=("QUARTERLY_AVG", "MONTH_END"), default="QUARTERLY_AVG")
        parser.add_argument("--quarter-label", default=""); parser.add_argument("--amc", default="")
        parser.add_argument("--source-url", default="", help="Official disclosure URL when --from-file is used.")
        parser.add_argument("--amount-unit", choices=("AUTO", "RUPEES", "LAKH", "CRORE"), default="AUTO")
        parser.add_argument("--limit", type=int, default=0); parser.add_argument("--dry-run", action="store_true")
    def handle(self, *args, **options):
        try:
            as_of = date.fromisoformat(options["date"])
            payload, filename, downloaded_url = load_source_payload(source=f"aum/{options['aum_type'].lower()}", as_of_date=as_of, from_file=options["from_file"] or "", url=options["url"] or "")
            source_url = resolve_source_url(from_file=options["from_file"] or "", supplied_source_url=options["source_url"], downloaded_url=downloaded_url)
        except Exception as exc: raise CommandError(str(exc)) from exc
        run = None if options["dry_run"] else start_source_run(source=f"AMFI_{options['aum_type']}", command="sync_mf_aum", as_of_date=as_of, source_url=source_url, raw_file_path=options["from_file"] or filename)
        inserted = updated = unmatched = skipped = parsed = 0; errors = []
        if options["aum_type"] == "QUARTERLY_AVG":
            if as_of.month not in {1, 4, 7, 10} or as_of.day != 1:
                raise CommandError("--date for QUARTERLY_AVG must be a quarter start: Jan 1, Apr 1, Jul 1 or Oct 1.")
            from mutual_funds.management.commands.import_amfi_scheme_aaum import parse_amfi_scheme_rows
            rows = parse_amfi_scheme_rows(payload)
            if options["limit"] > 0: rows = rows[:options["limit"]]
            parsed = len(rows)
            label = options["quarter_label"] or quarter_label(as_of)
            args = {"quarter_start": as_of.isoformat(), "quarter_label": label, "dry_run": options["dry_run"]}
            args["file" if options["from_file"] else "url"] = options["from_file"] or options["url"]
            if options["dry_run"]:
                matched = 0
                for name, amount in rows:
                    scheme, _ = resolve_master_scheme(isin="", scheme_name=name)
                    if not scheme:
                        continue
                    if options["amc"] and options["amc"].casefold() not in (scheme.amc_name or "").casefold():
                        continue
                    matched += 1
                    self.stdout.write(f"{scheme.scheme_code} | ₹{amount / 100:,.2f} Cr")
                self.stdout.write(self.style.SUCCESS(f"Quarterly AAUM dry-run: parsed={parsed}, matched={matched}; no records were written."))
                return
            for name, amount_lakh in rows:
                scheme, status = resolve_master_scheme(isin="", scheme_name=name)
                if not scheme:
                    unmatched += 1
                    if len(errors) < 20: errors.append({"scheme": name, "status": status})
                    continue
                if options["amc"] and options["amc"].casefold() not in (scheme.amc_name or "").casefold():
                    skipped += 1; continue
                value_cr = (amount_lakh / Decimal("100")).quantize(Decimal("0.0001"))
                exists = AMFISchemeAAUM.objects.filter(scheme=scheme, quarter_start=as_of, source="AMFI_SCHEME_AAUM").exists()
                AMFISchemeAAUM.objects.update_or_create(
                    scheme=scheme, quarter_start=as_of, source="AMFI_SCHEME_AAUM",
                    defaults={"quarter_label": label, "average_aum_cr": value_cr, "source_scheme_name": name},
                )
                upsert_masterdata_snapshot(
                    scheme, as_of_date=as_of,
                    values={"aum_cr": value_cr, "aum_type": "QUARTERLY_AVG", "aum_as_of": quarter_end(as_of), "aum_source": "AMFI_SCHEME_AAUM"},
                    source="AMFI_SCHEME_AAUM", source_url=source_url,
                    field_names=["aum_cr", "aum_type", "aum_as_of", "aum_source"],
                )
                inserted += int(not exists); updated += int(exists)
        else:
            if as_of.day != monthrange(as_of.year, as_of.month)[1]:
                raise CommandError("--date must be the exact calendar month-end for MONTH_END AUM.")
            rows = parse_aum_rows(payload, filename=filename, as_of_date=as_of, amount_unit=options["amount_unit"])
            if options["limit"] > 0: rows = rows[:options["limit"]]
            parsed = len(rows)
            for row in rows:
                if row["scheme_code"]:
                    scheme = AMFIMasterScheme.objects.filter(scheme_code=row["scheme_code"], is_active=True).first()
                    status = "scheme_code" if scheme else "unmatched"
                else:
                    scheme, status = resolve_master_scheme(isin=row["isin"], scheme_name=row["scheme_name"])
                if not scheme:
                    unmatched += 1
                    if len(errors) < 20: errors.append({"scheme": row["scheme_name"], "status": status})
                    continue
                if options["amc"] and options["amc"].casefold() not in (scheme.amc_name or "").casefold():
                    skipped += 1; continue
                _, created = upsert_masterdata_snapshot(
                    scheme, as_of_date=as_of,
                    values={"aum_cr": row["aum_cr"], "aum_type": "MONTH_END", "aum_as_of": as_of, "aum_source": "AMC_MONTH_END_AUM"},
                    source="AMC_MONTH_END_AUM", source_url=source_url,
                    field_names=["aum_cr", "aum_type", "aum_as_of", "aum_source"],
                )
                inserted += int(created); updated += int(not created)
        self.stdout.write(self.style.SUCCESS(f"AUM type={options['aum_type']} parsed={parsed} inserted={inserted} updated={updated} unmatched={unmatched} skipped={skipped} dry_run={options['dry_run']}"))
        if run:
            finish_source_run(run, status=MFSourceRun.Status.PARTIAL if unmatched else MFSourceRun.Status.SUCCESS, parsed=parsed, inserted=inserted, updated=updated, skipped=skipped, unmatched=unmatched, errors=errors, metadata={"aum_type": options["aum_type"]})
