"""Add AMFIMasterScheme.amc_name in a way that works on a drifted database.

NEEDS APPROVAL BEFORE RUNNING ON A REAL DATABASE.

Some installs already have a NOT NULL ``amc_name`` column that no migration on
this branch created (it came from earlier, unmerged work). Inserts from this
branch then fail with "null value in column amc_name". This migration:

* adds the column (VARCHAR(300), empty string for existing rows) when it is
  missing, or
* keeps an existing column, sets any NULL values to '' and, on PostgreSQL,
  widens it to VARCHAR(300) if it is shorter;

and in both cases records the field in Django's model state. The model always
sends a value (default ''), so inserts never send NULL again. AMC names are
filled from NAVAll.txt headings on the next AMFI refresh.

Reverse: removes the field from Django's state only and leaves the column, so
rolling back never drops data.
"""
from django.db import migrations, models

TABLE = "mutual_funds_amfimasterscheme"
COLUMN = "amc_name"
LENGTH = 300


def _columns(schema_editor):
    connection = schema_editor.connection
    with connection.cursor() as cursor:
        return {column.name: column for column in connection.introspection.get_table_description(cursor, TABLE)}


def ensure_amc_name_column(schema_editor):
    """Return "added" or "exists" (also used by the tests)."""
    columns = _columns(schema_editor)
    quote = schema_editor.quote_name
    if COLUMN not in columns:
        vendor = schema_editor.connection.vendor
        if vendor == "postgresql":
            schema_editor.execute(
                f"ALTER TABLE {quote(TABLE)} ADD COLUMN {quote(COLUMN)} varchar({LENGTH}) NOT NULL DEFAULT ''"
            )
            schema_editor.execute(f"ALTER TABLE {quote(TABLE)} ALTER COLUMN {quote(COLUMN)} DROP DEFAULT")
        else:
            schema_editor.execute(
                f"ALTER TABLE {quote(TABLE)} ADD COLUMN {quote(COLUMN)} varchar({LENGTH}) NOT NULL DEFAULT ''"
            )
        return "added"

    schema_editor.execute(f"UPDATE {quote(TABLE)} SET {quote(COLUMN)} = '' WHERE {quote(COLUMN)} IS NULL")
    if schema_editor.connection.vendor == "postgresql":
        with schema_editor.connection.cursor() as cursor:
            cursor.execute(
                "SELECT data_type, character_maximum_length FROM information_schema.columns "
                "WHERE table_schema = current_schema() AND table_name = %s AND column_name = %s",
                [TABLE, COLUMN],
            )
            row = cursor.fetchone()
        data_type, length = (row or (None, None))
        if data_type in ("character varying", "character") and length is not None and int(length) < LENGTH:
            schema_editor.execute(
                f"ALTER TABLE {quote(TABLE)} ALTER COLUMN {quote(COLUMN)} TYPE varchar({LENGTH})"
            )
    return "exists"


def forwards(apps, schema_editor):
    result = ensure_amc_name_column(schema_editor)
    print(f"\n  AMFIMasterScheme.amc_name: column {result}.")


class Migration(migrations.Migration):

    dependencies = [
        ("mutual_funds", "0015_financial_precision_4dp"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[migrations.RunPython(forwards, migrations.RunPython.noop)],
            state_operations=[
                migrations.AddField(
                    model_name="amfimasterscheme",
                    name="amc_name",
                    field=models.CharField(blank=True, default="", max_length=300),
                ),
            ],
        ),
    ]
