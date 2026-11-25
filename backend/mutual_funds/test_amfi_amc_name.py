"""AMFI master: amc_name from NAVAll.txt headings, and a drift-safe migration."""
import importlib
from datetime import date

from django.db import connection
from django.test import SimpleTestCase, TestCase

from mutual_funds.models import AMFIMasterScheme
from mutual_funds.services.amfi import AMFIService

NAV_ALL_SAMPLE = """Scheme Code;ISIN Div Payout/ ISIN Growth;ISIN Div Reinvestment;Scheme Name;Plan;Option;Net Asset Value;Date

Open Ended Schemes(Debt Scheme - Banking and PSU Fund)

Bandhan Mutual Fund

121279;INF194K015G8;-;Bandhan Banking and PSU Debt Fund;Direct Plan;Growth;27.3204;23-Nov-2026

Open Ended Schemes(Equity Scheme - Flexi Cap Fund)

PPFAS Mutual Fund

122639;INF879O01027;-;Parag Parikh Flexi Cap Fund;Direct Plan;Growth;94.9255;23-Nov-2026
"""


class NavFileAmcHeadingTests(SimpleTestCase):
    def test_each_record_carries_the_amc_heading_above_it(self):
        records = AMFIService.parse_nav_file(NAV_ALL_SAMPLE, historical=False)
        self.assertEqual({record["scheme_code"]: record["amc_name"] for record in records},
                         {"121279": "Bandhan Mutual Fund", "122639": "PPFAS Mutual Fund"})

    def test_category_headings_are_not_taken_as_amc_names(self):
        records = AMFIService.parse_nav_file(NAV_ALL_SAMPLE, historical=False)
        self.assertTrue(all("Schemes(" not in record["amc_name"] for record in records))


class MasterImportAmcNameTests(TestCase):
    def test_amc_name_is_stored_and_not_wiped_by_a_record_without_it(self):
        records = AMFIService.parse_nav_file(NAV_ALL_SAMPLE, historical=False)
        AMFIService._import_master_records(records)
        self.assertEqual(AMFIMasterScheme.objects.get(scheme_code="121279").amc_name, "Bandhan Mutual Fund")

        AMFIService._import_master_records([{
            "scheme_code": "121279", "scheme_name": "Bandhan Banking and PSU Debt Fund;Direct Plan;Growth",
            "isin_growth": "INF194K015G8", "isin_dividend": None, "nav": 27, "date": date(2026, 11, 24),
        }])
        self.assertEqual(AMFIMasterScheme.objects.get(scheme_code="121279").amc_name, "Bandhan Mutual Fund")

    def test_scheme_without_a_heading_gets_an_empty_name_not_null(self):
        AMFIService._import_master_records([{
            "scheme_code": "999999", "scheme_name": "Orphan Scheme", "isin_growth": None,
            "isin_dividend": None, "nav": 10, "date": date(2026, 11, 24),
        }])
        self.assertEqual(AMFIMasterScheme.objects.get(scheme_code="999999").amc_name, "")


class _CursorEditor:
    """The subset of a schema editor the migration helper uses."""

    def __init__(self, conn):
        self.connection = conn

    def quote_name(self, name):
        return self.connection.ops.quote_name(name)

    def execute(self, sql):
        with self.connection.cursor() as cursor:
            cursor.execute(sql)


class DriftSafeMigrationTests(TestCase):
    def test_column_step_is_idempotent_when_the_column_already_exists(self):
        migration = importlib.import_module("mutual_funds.migrations.0016_amfimasterscheme_amc_name_reconcile")
        AMFIMasterScheme.objects.create(scheme_code="1", scheme_name="Existing", amc_name="Kept AMC")
        self.assertEqual(migration.ensure_amc_name_column(_CursorEditor(connection)), "exists")
        self.assertEqual(migration.ensure_amc_name_column(_CursorEditor(connection)), "exists")
        self.assertEqual(AMFIMasterScheme.objects.get(scheme_code="1").amc_name, "Kept AMC")
        with connection.cursor() as cursor:
            columns = {col.name for col in connection.introspection.get_table_description(cursor, AMFIMasterScheme._meta.db_table)}
        self.assertIn("amc_name", columns)
