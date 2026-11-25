"""Shared helpers for the PWMS database reset management commands.

These commands intentionally preserve the django_migrations table. They operate
on the configured database alias and fail closed when a reset would violate a
foreign-key dependency.
"""
from __future__ import annotations

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.management.base import CommandError
from django.db import connections
from django.db.migrations.recorder import MigrationRecorder


INTERNAL_TABLES = {
    "sqlite_sequence",
    "sqlite_stat1",
    "sqlite_stat4",
}


def get_physical_tables(alias: str) -> list[str]:
    connection = connections[alias]
    with connection.cursor() as cursor:
        names = connection.introspection.table_names(cursor)
    migration_table = MigrationRecorder.Migration._meta.db_table
    return sorted(
        name for name in names
        if name not in INTERNAL_TABLES and name != migration_table
    )


def get_account_preservation_tables(alias: str) -> set[str]:
    """Return account/authentication tables, not personal-finance tables."""
    preserved = {get_user_model()._meta.db_table}

    # Preserve account identity, access-control, family and preference data.
    for model_name in (
        "UserProfile",
        "FamilyGroup",
        "FamilyMembership",
        "UserPreference",
        "UserAuditLog",
    ):
        try:
            model = apps.get_model("users", model_name)
        except LookupError:
            continue
        if model._meta.managed:
            preserved.add(model._meta.db_table)

    # Preserve Django authorization metadata.
    for label in ("auth.Group", "auth.Permission", "contenttypes.ContentType"):
        try:
            model = apps.get_model(label)
        except LookupError:
            continue
        if model._meta.managed:
            preserved.add(model._meta.db_table)

    # Preserve M2M through tables for users and groups. Forward M2M fields
    # are not auto-created; their implicit through models are.
    for model in (get_user_model(), apps.get_model("auth", "Group")):
        for field in model._meta.get_fields():
            if (
                getattr(field, "many_to_many", False)
                and not getattr(field, "auto_created", False)
            ):
                through = field.remote_field.through
                if through._meta.managed:
                    preserved.add(through._meta.db_table)

    return preserved


def find_truncate_blockers(alias: str, reset_tables: set[str]) -> list[tuple[str, str, str]]:
    """Find preserved child tables that reference a table in the truncate set."""
    connection = connections[alias]
    all_tables = get_physical_tables(alias)
    blockers: list[tuple[str, str, str]] = []

    with connection.cursor() as cursor:
        for child_table in all_tables:
            if child_table in reset_tables:
                continue
            constraints = connection.introspection.get_constraints(cursor, child_table)
            for constraint_name, details in constraints.items():
                foreign_key = details.get("foreign_key")
                if foreign_key and foreign_key[0] in reset_tables:
                    blockers.append((child_table, constraint_name, foreign_key[0]))

    return blockers


def truncate_tables(alias: str, tables: list[str]) -> None:
    """Delete rows and reset identities without using TRUNCATE CASCADE."""
    if not tables:
        return

    connection = connections[alias]

    if connection.vendor == "postgresql":
        quoted = [connection.ops.quote_name(name) for name in tables]
        sql = "TRUNCATE TABLE " + ", ".join(quoted) + " RESTART IDENTITY"
        with connection.cursor() as cursor:
            cursor.execute(sql)
        return

    if connection.vendor == "sqlite":
        with connection.cursor() as cursor:
            # Defer FK validation to transaction commit while every table in
            # the reset set is cleared. Preserved parents remain untouched.
            cursor.execute("PRAGMA defer_foreign_keys = ON")
            for table in tables:
                cursor.execute(f"DELETE FROM {connection.ops.quote_name(table)}")

            cursor.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = %s",
                ["sqlite_sequence"],
            )
            if cursor.fetchone():
                placeholders = ", ".join(["%s"] * len(tables))
                cursor.execute(
                    f"DELETE FROM sqlite_sequence WHERE name IN ({placeholders})",
                    tables,
                )
        return

    raise CommandError(
        f"Database vendor '{connection.vendor}' is not supported by PWMS reset commands. "
        "Use PostgreSQL or SQLite."
    )


def validate_truncate_scope(alias: str, reset_tables: set[str]) -> None:
    blockers = find_truncate_blockers(alias, reset_tables)
    if blockers:
        formatted = "\n".join(
            f"  - {child}.{constraint} references {parent}"
            for child, constraint, parent in blockers
        )
        raise CommandError(
            "Reset aborted: preserved tables reference tables selected for deletion. "
            "No data was changed. Review these foreign keys before changing the reset scope:\n"
            + formatted
        )


def prompt_for_confirmation(command, alias: str, description: str) -> bool:
    if command.noinput:
        return True
    connection = connections[alias]
    database_name = connection.settings_dict.get("NAME", alias)
    command.stdout.write(
        command.style.WARNING(
            f"You are about to {description} in database '{database_name}'."
        )
    )
    answer = input("Type 'yes' to continue, or anything else to cancel: ")
    if answer != "yes":
        command.stdout.write("Reset cancelled; no data was changed.")
        return False
    return True
