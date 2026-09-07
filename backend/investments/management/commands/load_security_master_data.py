import json
from pathlib import Path

from django.core.management.base import BaseCommand

from investments.models import SecurityMaster


DATA_FILE = Path(__file__).resolve().parent.parent.parent / "data" / "security_master_lookups.json"


class Command(BaseCommand):
    """
    Load web-researched sector / cap_type / pe_ratio / pb_ratio / roe
    values into SecurityMaster from investments/data/
    security_master_lookups.json, matched by ISIN.

    This file is built up incrementally (a batch of stocks at a
    time), each entry carrying the sources it was cross-checked
    against and the date it was current as of — see that file's
    _comment for the full rationale on why ratios are dated
    snapshots rather than live data.

    Dry-run by default: prints exactly what would change per field
    (old value -> new value) without writing anything. Pass --apply
    to actually save. Never overwrites a field that already has a
    non-empty value unless --overwrite is also passed, so re-running
    this after someone has manually corrected a value in Django
    admin won't clobber their correction.
    """

    help = (
        "Load researched sector/cap_type/pe/pb/roe values into "
        "SecurityMaster from investments/data/security_master_lookups.json. "
        "Dry-run by default; pass --apply to write."
    )

    def add_arguments(self, parser):

        parser.add_argument(
            "--apply",
            action="store_true",
            help="Actually save the changes. Without this flag, only prints a report.",
        )

        parser.add_argument(
            "--overwrite",
            action="store_true",
            help="Also overwrite fields that already have a non-empty value.",
        )

    def handle(self, *args, **options):

        apply_changes = options.get("apply", False)
        overwrite = options.get("overwrite", False)

        if not DATA_FILE.exists():
            self.stderr.write(self.style.ERROR(f"Data file not found: {DATA_FILE}"))
            return

        with open(DATA_FILE, encoding="utf-8") as f:
            payload = json.load(f)

        entries = payload.get("entries", {})

        fields = [
            "sector",
            "cap_type",
            "pe_ratio",
            "pb_ratio",
            "roe",
            "amc_name",
            "credit_rating",
            "ytm",
            "modified_duration",
            "average_maturity",
        ]

        updated = 0
        not_found = []
        skipped_populated = []

        for isin, data in entries.items():

            security_masters = SecurityMaster.objects.filter(isin=isin)

            if not security_masters.exists():
                not_found.append((isin, data.get("asset_name", "")))
                continue

            for sm in security_masters:

                changes = {}

                for field in fields:
                    new_value = data.get(field)

                    if new_value is None:
                        continue

                    current_value = getattr(sm, field)

                    already_populated = current_value not in (None, "")

                    if already_populated and not overwrite:
                        continue

                    if str(current_value) == str(new_value):
                        continue

                    changes[field] = (current_value, new_value)

                if not changes:
                    if any(getattr(sm, f) not in (None, "") for f in fields):
                        skipped_populated.append(sm.asset_name)
                    continue

                self.stdout.write(
                    f"  {sm.asset_name!r} (ISIN {isin}, owner #{sm.owner_id}) "
                    f"[sources: {', '.join(data.get('sources', []))}, "
                    f"as of {data.get('as_of', 'unknown')}]"
                )

                for field, (old, new) in changes.items():
                    self.stdout.write(f"      {field}: {old!r} -> {new!r}")

                    if apply_changes:
                        setattr(sm, field, new)

                if apply_changes:
                    sm.save(update_fields=list(changes.keys()))

                updated += 1

        self.stdout.write("")

        if apply_changes:
            self.stdout.write(self.style.SUCCESS(f"Updated {updated} SecurityMaster row(s)."))
        else:
            self.stdout.write(
                self.style.WARNING(
                    f"DRY RUN — would update {updated} row(s). Re-run with --apply to save."
                )
            )

        if skipped_populated:
            self.stdout.write(
                f"{len(skipped_populated)} row(s) already have values and were left alone "
                "(pass --overwrite to replace them): " + ", ".join(skipped_populated)
            )

        if not_found:
            self.stdout.write(
                self.style.WARNING(
                    f"{len(not_found)} ISIN(s) in the data file have no matching "
                    "SecurityMaster row:"
                )
            )

            for isin, name in not_found:
                self.stdout.write(f"  {name!r} (ISIN {isin})")
