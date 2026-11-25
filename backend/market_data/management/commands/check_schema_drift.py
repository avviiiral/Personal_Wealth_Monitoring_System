"""Compare the database schema with this branch's models.

Read-only by default. Reports, per model table:

* extra columns the models do not know (left by another branch's migrations).
  A NOT NULL extra column without a database default BREAKS INSERTS from this
  branch ("null value in column ... violates not-null constraint");
* model columns missing from the database (run migrate);
* applied migrations that do not exist on this branch;
* tables no model on this branch uses (only without --app).

--apply-safe-defaults (PostgreSQL only) gives each insert-breaking column a
type-appropriate default ('' for text, false for boolean, 0 for numbers) in one
transaction. It never drops a column, never changes existing rows and never
changes NULL/NOT NULL. Columns with no safe default (dates, JSON, ...) are only
reported. Writes data/reports/schema_drift.csv.
"""
import csv
import io

from django.apps import apps
from django.core.management.base import BaseCommand
from django.db import connection, transaction
from django.db.migrations.loader import MigrationLoader

from config.scheduling.files import atomic_write_text, reports_dir

SAFE_DEFAULTS = {
    "character varying": "''",
    "character": "''",
    "text": "''",
    "boolean": "false",
    "smallint": "0",
    "integer": "0",
    "bigint": "0",
    "numeric": "0",
    "real": "0",
    "double precision": "0",
}


class Command(BaseCommand):
    help = "Read-only report of database columns this branch's models do not know (and optional safe defaults)."

    def add_arguments(self, parser):
        parser.add_argument("--app", action="append", default=[], help="Limit to an app label (repeatable).")
        parser.add_argument(
            "--apply-safe-defaults", action="store_true",
            help="PostgreSQL: set a type-appropriate DEFAULT on extra NOT NULL columns that break inserts.",
        )

    # Introspection -----------------------------------------------------------

    def _models(self, labels):
        for model in apps.get_models(include_auto_created=True):
            meta = model._meta
            if not meta.managed or meta.proxy:
                continue
            if labels and meta.app_label not in labels:
                continue
            yield model

    def _column_info(self, cursor, table):
        if connection.vendor == "postgresql":
            cursor.execute(
                "SELECT column_name, data_type, is_nullable, column_default FROM information_schema.columns "
                "WHERE table_schema = current_schema() AND table_name = %s",
                [table],
            )
            return {
                name: {"type": data_type, "nullable": nullable == "YES", "default": default}
                for name, data_type, nullable, default in cursor.fetchall()
            }
        return {
            column.name: {"type": str(column.type_code), "nullable": bool(column.null_ok), "default": column.default}
            for column in connection.introspection.get_table_description(cursor, table)
        }

    def handle(self, *args, **options):
        labels = set(options["app"])
        rows = []
        with connection.cursor() as cursor:
            tables = set(connection.introspection.table_names(cursor))
            known_tables = set()
            for model in self._models(labels):
                table = model._meta.db_table
                known_tables.add(table)
                if table not in tables:
                    rows.append({"table": table, "column": "", "issue": "missing table", "type": "",
                                 "action": "run migrate"})
                    continue
                info = self._column_info(cursor, table)
                model_columns = {field.column for field in model._meta.local_concrete_fields}
                for column in sorted(set(info) - model_columns):
                    details = info[column]
                    breaks = not details["nullable"] and details["default"] is None
                    safe = SAFE_DEFAULTS.get(details["type"]) if breaks else None
                    rows.append({
                        "table": table, "column": column, "type": details["type"],
                        "issue": "BREAKS INSERTS (NOT NULL, no default)" if breaks else "extra column (harmless)",
                        "action": (
                            f"ALTER TABLE {connection.ops.quote_name(table)} ALTER COLUMN "
                            f"{connection.ops.quote_name(column)} SET DEFAULT {safe}"
                            if safe else ("no safe default; decide by hand" if breaks else "")
                        ),
                        "safe_default": safe,
                    })
                for column in sorted(model_columns - set(info)):
                    rows.append({"table": table, "column": column, "type": "", "issue": "missing column",
                                 "action": "run migrate"})

        loader = MigrationLoader(connection, ignore_no_migrations=True)
        unknown_migrations = sorted(
            f"{app}.{name}" for app, name in loader.applied_migrations
            if (app, name) not in loader.disk_migrations and (not labels or app in labels)
            and app in {config.label for config in apps.get_app_configs()}
        )
        unused_tables = [] if labels else sorted(
            table for table in tables - known_tables if table != "django_migrations"
        )

        self._print(rows, unknown_migrations, unused_tables)
        self._write_csv(rows)
        if options["apply_safe_defaults"]:
            self._apply(rows)

    # Output ------------------------------------------------------------------

    def _print(self, rows, unknown_migrations, unused_tables):
        out = self.stdout
        if not rows:
            out.write("No column drift: every model table matches this branch's models.")
        breaking = [row for row in rows if row["issue"].startswith("BREAKS")]
        if rows:
            out.write(f"Column drift: {len(rows)} finding(s), {len(breaking)} break inserts from this branch.")
            for row in rows:
                out.write(f"  {row['table']}.{row['column']}  [{row['type']}]  {row['issue']}")
                if row["action"]:
                    out.write(f"      -> {row['action']}")
        if unknown_migrations:
            out.write(f"Applied migrations not on this branch ({len(unknown_migrations)}):")
            for name in unknown_migrations:
                out.write(f"  {name}")
        if unused_tables:
            out.write(f"Tables no model on this branch uses ({len(unused_tables)}): {', '.join(unused_tables)}")

    def _write_csv(self, rows):
        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=["table", "column", "type", "issue", "action"], extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
        path = reports_dir() / "schema_drift.csv"
        try:
            atomic_write_text(path, buffer.getvalue())
            self.stdout.write(f"Details: {path}")
        except OSError as exc:
            self.stderr.write(f"Could not write {path}: {exc}")

    def _apply(self, rows):
        if connection.vendor != "postgresql":
            self.stdout.write("--apply-safe-defaults is supported on PostgreSQL only; nothing changed.")
            return
        statements = [row["action"] for row in rows if row.get("safe_default")]
        if not statements:
            self.stdout.write("Nothing to apply.")
            return
        with transaction.atomic(), connection.cursor() as cursor:
            for statement in statements:
                cursor.execute(statement)
        self.stdout.write(self.style.SUCCESS(f"Applied {len(statements)} default(s). No column or row was removed."))
        manual = [row for row in rows if row["issue"].startswith("BREAKS") and not row.get("safe_default")]
        for row in manual:
            self.stdout.write(f"  still breaks inserts (no safe default): {row['table']}.{row['column']} [{row['type']}]")
