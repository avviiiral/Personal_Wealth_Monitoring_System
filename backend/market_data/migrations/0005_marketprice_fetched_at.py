from django.db import migrations, models


class Migration(migrations.Migration):
    """Additive and nullable: existing rows keep fetched_at = NULL.

    NULL means "fetch time unknown"; the price job treats such rows as stale
    and refreshes them once.
    """

    dependencies = [
        ("market_data", "0004_performance_indexes"),
    ]

    operations = [
        migrations.AddField(
            model_name="marketprice",
            name="fetched_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
