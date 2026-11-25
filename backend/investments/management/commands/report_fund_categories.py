"""Read-only: list funds that migration 0111 would move (or most likely moved) to MUTUAL_FUND."""
import csv
import io

from django.core.management.base import BaseCommand

from config.scheduling.files import atomic_write_text, reports_dir
from investments.models import Asset, Transaction
from investments.services.fund_category_repair import find_likely_repaired_funds, find_mislabelled_funds


class Command(BaseCommand):
    help = (
        "List mutual funds stored with category BOND/STOCK/CASH that migration "
        "investments.0111 would change to MUTUAL_FUND. Changes nothing."
    )

    def add_arguments(self, parser):
        parser.add_argument("--csv", action="store_true", help="Also write data/reports/fund_category_repair.csv.")
        parser.add_argument(
            "--already-applied", action="store_true",
            help="After 0111 has run: list the assets it most likely changed to MUTUAL_FUND "
                 "(writes fund_category_repair_applied.csv with --csv).",
        )

    def handle(self, *args, **options):
        if options["already_applied"]:
            return self._already_applied(options["csv"])
        report = find_mislabelled_funds(Asset, Transaction)
        self.stdout.write(f"Would change {len(report['change'])} asset(s) to MUTUAL_FUND:")
        for item in report["change"]:
            self.stdout.write(
                f"  #{item['asset_id']:<6} family={item['family_id']}  {item['old_category']:<5} -> MUTUAL_FUND  "
                f"{item['isin'] or '(no ISIN)':<13}  {item['name']}"
            )
        self.stdout.write(f"Left unchanged ({len(report['conflicts'])}):")
        for item in report["conflicts"]:
            self.stdout.write(
                f"  #{item['asset_id']:<6} family={item['family_id']}  {item['old_category']:<5}  "
                f"{item['isin'] or '(no ISIN)':<13}  {item['name']}  [{item['reason']}]"
            )
        if options["csv"]:
            rows = [{"action": "change", "reason": "", **item} for item in report["change"]]
            rows += [{"action": "unchanged", **item} for item in report["conflicts"]]
            self._write_csv(
                "fund_category_repair.csv",
                ["action", "asset_id", "family_id", "name", "isin", "old_category", "new_category", "reason"],
                rows,
            )

    def _write_csv(self, filename, fieldnames, rows):
        """Write a report CSV atomically; a file open in Excel is reported, not raised."""
        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
        path = reports_dir() / filename
        try:
            atomic_write_text(path, buffer.getvalue())
        except OSError as exc:
            self.stderr.write(f"Could not write {path}: {exc}. If the file is open, close it in Excel and run again.")
            return
        self.stdout.write(f"Wrote {path}")

    def _already_applied(self, write_csv):
        from django.db.migrations.recorder import MigrationRecorder

        record = MigrationRecorder.Migration.objects.filter(
            app="investments", name="0111_repair_mutual_fund_categories"
        ).first()
        if record is None:
            self.stdout.write("investments.0111 has not been applied yet; run without --already-applied.")
            return
        from config.scheduling.clock import localize

        listed = find_likely_repaired_funds(Asset, Transaction, record.applied)
        self.stdout.write(
            f"investments.0111 applied {localize(record.applied):%Y-%m-%d %H:%M %Z}. "
            f"Most likely changed {len(listed)} asset(s) to MUTUAL_FUND (inferred; 0111 kept no record):"
        )
        for item in listed:
            self.stdout.write(
                f"  #{item['asset_id']:<6} family={item['family_id']}  {item['old_category']:<5} -> MUTUAL_FUND  "
                f"{item['isin'] or '(no ISIN)':<13}  {item['name']}"
            )
        if write_csv:
            self._write_csv(
                "fund_category_repair_applied.csv",
                ["asset_id", "family_id", "name", "isin", "old_category", "new_category"],
                listed,
            )
