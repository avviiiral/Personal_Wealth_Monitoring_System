"""Base implementation for PWMS reset commands."""
from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError
from django.core.management.sql import emit_post_migrate_signal
from django.db import connections, transaction

from users.management.reset_utils import (
    get_account_preservation_tables,
    get_physical_tables,
    prompt_for_confirmation,
    truncate_tables,
    validate_truncate_scope,
)


class PhysicalTableResetCommand(BaseCommand):
    """Reset physical database tables while retaining Django migrations."""

    noinput = False
    scope = "all"
    description = "clear all application data"
    recreate_post_migrate_data = False

    def add_arguments(self, parser):
        # Match Django's built-in flush options so test runners and existing
        # call_command("flush", ...) invocations remain compatible.
        parser.add_argument(
            "--noinput",
            "--no-input",
            action="store_false",
            dest="interactive",
            default=True,
            help="Run without asking for confirmation.",
        )
        parser.add_argument(
            "--database",
            default="default",
            help="Database alias to reset (default: default).",
        )
        parser.add_argument(
            "--reset-sequences",
            action="store_true",
            help="Reset auto-increment sequences after clearing data.",
        )
        parser.add_argument(
            "--allow-cascade",
            action="store_true",
            help="Not supported: PWMS reset commands never use TRUNCATE CASCADE.",
        )
        parser.add_argument(
            "--inhibit-post-migrate",
            action="store_true",
            dest="inhibit_post_migrate",
            help="Do not emit post_migrate signals after the reset.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show the exact tables selected without deleting data.",
        )

    def get_reset_plan(self, alias: str) -> tuple[list[str], set[str]]:
        tables = get_physical_tables(alias)
        if self.scope == "all":
            return tables, set(tables)

        if self.scope == "account":
            preserved = get_account_preservation_tables(alias)
            reset_tables = sorted(set(tables) - preserved)
            return reset_tables, set(reset_tables)

        raise CommandError(f"Unsupported reset scope: {self.scope}")

    def handle(self, *args, **options):
        alias = options["database"]
        if alias not in connections:
            raise CommandError(f"Unknown database alias: {alias}")

        self.noinput = not options["interactive"]
        if options["allow_cascade"]:
            raise CommandError(
                "--allow-cascade is deliberately unsupported. "
                "PWMS reset commands enumerate dependent tables and never "
                "truncate with CASCADE."
            )

        connection = connections[alias]
        if connection.vendor not in {"postgresql", "sqlite"}:
            raise CommandError(
                f"Unsupported database vendor '{connection.vendor}'. "
                "PWMS reset commands support PostgreSQL and SQLite."
            )

        tables, reset_set = self.get_reset_plan(alias)
        if not tables:
            self.stdout.write(self.style.WARNING("No physical tables selected for reset."))
            return

        validate_truncate_scope(alias, reset_set)

        self.stdout.write(f"Database alias: {alias}")
        self.stdout.write(f"Database engine: {connection.vendor}")
        self.stdout.write(f"Tables selected: {len(tables)}")
        self.stdout.write("Migration history: PRESERVED")
        self.stdout.write("Schema: PRESERVED")
        if self.scope == "account":
            preserved = sorted(set(get_physical_tables(alias)) - reset_set)
            self.stdout.write(f"Tables preserved: {len(preserved)}")
            self.stdout.write("Preserved account/auth tables:")
            for name in preserved:
                self.stdout.write(f"  KEEP  {name}")

        if options["dry_run"]:
            self.stdout.write("Tables selected for deletion:")
            for name in tables:
                self.stdout.write(f"  DELETE  {name}")
            self.stdout.write(self.style.SUCCESS("Dry run complete. No data was changed."))
            return

        if not prompt_for_confirmation(self, alias, self.description):
            return

        try:
            with transaction.atomic(using=alias):
                truncate_tables(
                    alias,
                    tables,
                    reset_sequences=options["reset_sequences"],
                )
        except Exception as exc:
            raise CommandError(
                "Reset failed and the database transaction was rolled back. "
                f"Original error: {exc}"
            ) from exc

        if self.recreate_post_migrate_data and not options["inhibit_post_migrate"]:
            emit_post_migrate_signal(
                verbosity=options["verbosity"],
                interactive=not self.noinput,
                db=alias,
            )

        self.stdout.write(
            self.style.SUCCESS(
                f"Reset completed. Cleared {len(tables)} tables; "
                "database schema and django_migrations were preserved."
            )
        )
