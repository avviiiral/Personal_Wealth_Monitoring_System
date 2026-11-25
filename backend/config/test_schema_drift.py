"""check_schema_drift: find database columns this branch's models do not know.

A database migrated by another branch can carry NOT NULL columns without a
default. Inserts from this branch then fail ("null value in column ...").
The command reports them (read-only) and, with --apply-safe-defaults on
PostgreSQL, gives them a type-appropriate DB default. It never drops a
column, never changes data, never changes NULL/NOT NULL.
"""
import unittest
from io import StringIO

from django.core.management import call_command
from django.db import connection
from django.test import TestCase

from mutual_funds.models import AMFIMasterScheme

TABLE = "mutual_funds_amfimasterscheme"


def _execute(sql):
    with connection.cursor() as cursor:
        cursor.execute(sql)


class SchemaDriftReportTests(TestCase):
    def test_clean_schema_reports_no_drift(self):
        out = StringIO()
        call_command("check_schema_drift", "--app", "mutual_funds", stdout=out)
        self.assertIn("No column drift", out.getvalue())

    def test_unknown_applied_migrations_are_listed(self):
        _execute(
            "INSERT INTO django_migrations (app, name, applied) "
            "VALUES ('mutual_funds', '0026_from_another_branch', CURRENT_TIMESTAMP)"
        )
        out = StringIO()
        call_command("check_schema_drift", "--app", "mutual_funds", stdout=out)
        self.assertIn("mutual_funds.0026_from_another_branch", out.getvalue())

    def test_extra_nullable_column_is_reported_as_harmless(self):
        _execute(f"ALTER TABLE {TABLE} ADD COLUMN extra_note varchar(20) NULL")
        out = StringIO()
        call_command("check_schema_drift", "--app", "mutual_funds", stdout=out)
        report = out.getvalue()
        self.assertIn(f"{TABLE}.extra_note", report)
        self.assertIn("harmless", report)


@unittest.skipUnless(connection.vendor == "postgresql", "column defaults can only be altered on PostgreSQL")
class SchemaDriftSafeDefaultTests(TestCase):
    def setUp(self):
        _execute(
            f"ALTER TABLE {TABLE} ADD COLUMN raw_category_header varchar(300) NOT NULL, "
            "ADD COLUMN navall_active boolean NOT NULL, ADD COLUMN rank_value integer NOT NULL, "
            "ADD COLUMN amfi_source_date date NOT NULL"
        )

    def _create_scheme(self):
        AMFIMasterScheme.objects.create(scheme_code="121279", scheme_name="Example Fund")

    def test_not_null_columns_without_default_are_flagged_and_break_inserts(self):
        out = StringIO()
        call_command("check_schema_drift", "--app", "mutual_funds", stdout=out)
        report = out.getvalue()
        self.assertIn(f"{TABLE}.raw_category_header", report)
        self.assertIn("BREAKS INSERTS", report)
        self.assertIn("ALTER TABLE", report)  # the SQL it would run is shown
        from django.db import IntegrityError, transaction

        with self.assertRaises(IntegrityError), transaction.atomic():
            self._create_scheme()

    def test_dry_run_changes_nothing(self):
        call_command("check_schema_drift", "--app", "mutual_funds", stdout=StringIO())
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT column_default FROM information_schema.columns "
                "WHERE table_name = %s AND column_name = 'raw_category_header'", [TABLE],
            )
            self.assertIsNone(cursor.fetchone()[0])

    def test_apply_sets_type_safe_defaults_and_inserts_work(self):
        from django.db import IntegrityError, transaction

        out = StringIO()
        call_command("check_schema_drift", "--app", "mutual_funds", "--apply-safe-defaults", stdout=out)
        report = out.getvalue()
        # A date has no safe default: reported, left alone, inserts still fail.
        self.assertIn("amfi_source_date", report)
        self.assertIn("no safe default", report)
        with self.assertRaises(IntegrityError), transaction.atomic():
            self._create_scheme()
        _execute(f"ALTER TABLE {TABLE} ALTER COLUMN amfi_source_date DROP NOT NULL")
        self._create_scheme()
        with connection.cursor() as cursor:
            cursor.execute(f"SELECT raw_category_header, navall_active, rank_value FROM {TABLE}")
            self.assertEqual(cursor.fetchone(), ("", False, 0))
