from django.db import migrations


def backfill_connection_metadata(apps, schema_editor):
    PortfolioNewsMatch = apps.get_model("portfolio_news", "PortfolioNewsMatch")
    PortfolioNewsMatch.objects.filter(connection_type__isnull=True).update(
        connection_type="direct",
        underlying_name="",
        underlying_weight=None,
    )


class Migration(migrations.Migration):

    dependencies = [
        ("portfolio_news", "0014_portfolionewsmatch_connection_unique"),
    ]

    operations = [
        migrations.RunPython(
            backfill_connection_metadata,
            migrations.RunPython.noop,
        ),
    ]
