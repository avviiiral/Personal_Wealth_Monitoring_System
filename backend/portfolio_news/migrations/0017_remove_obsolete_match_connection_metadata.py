from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("portfolio_news", "0016_merge_20261002_1209"),
    ]

    operations = [
        migrations.RunSQL(
            sql=[
                "DROP INDEX IF EXISTS unique_legacy_raw_news_match_connection;",
                "DROP INDEX IF EXISTS unique_family_raw_news_match_connection;",
                "ALTER TABLE portfolio_news_portfolionewsmatch DROP COLUMN IF EXISTS connection_type;",
                "ALTER TABLE portfolio_news_portfolionewsmatch DROP COLUMN IF EXISTS underlying_name;",
                "ALTER TABLE portfolio_news_portfolionewsmatch DROP COLUMN IF EXISTS underlying_weight;",
            ],
            reverse_sql=migrations.RunSQL.noop,
        ),
    ]
