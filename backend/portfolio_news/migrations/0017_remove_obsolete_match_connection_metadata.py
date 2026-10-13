from django.db import migrations


def remove_obsolete_match_metadata(apps, schema_editor):
    """Remove legacy columns only when they exist on the selected DB backend.

    The fields were removed from the Django model earlier, so a clean database
    may not have these physical columns at all. SQLite supports DROP COLUMN
    but does not support PostgreSQL-style DROP COLUMN IF EXISTS.
    """
    connection = schema_editor.connection
    table_name = "portfolio_news_portfolionewsmatch"
    quote_name = connection.ops.quote_name

    with connection.cursor() as cursor:
        # These names refer to legacy standalone indexes, not model constraints.
        cursor.execute(
            "DROP INDEX IF EXISTS "
            + quote_name("unique_legacy_raw_news_match_connection")
        )
        cursor.execute(
            "DROP INDEX IF EXISTS "
            + quote_name("unique_family_raw_news_match_connection")
        )

        if table_name not in connection.introspection.table_names(cursor):
            return

        existing_columns = {
            column.name
            for column in connection.introspection.get_table_description(
                cursor, table_name
            )
        }
        for column_name in (
            "connection_type",
            "underlying_name",
            "underlying_weight",
        ):
            if column_name not in existing_columns:
                continue
            schema_editor.execute(
                "ALTER TABLE "
                + quote_name(table_name)
                + " DROP COLUMN "
                + quote_name(column_name)
            )
            existing_columns.remove(column_name)


class Migration(migrations.Migration):

    dependencies = [
        ("portfolio_news", "0016_merge_20261002_1209"),
    ]

    operations = [
        migrations.RunPython(
            remove_obsolete_match_metadata,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
