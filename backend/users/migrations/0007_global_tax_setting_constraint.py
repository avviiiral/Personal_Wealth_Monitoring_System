from django.db import migrations, models


class Migration(migrations.Migration):
    # Keep the partial unique index in its own transaction, after the data
    # migration in 0006 has committed its row updates. PostgreSQL rejects
    # CREATE INDEX while those updates still have pending trigger events.

    dependencies = [
        ("users", "0006_global_asset_name_tax_settings"),
    ]

    operations = [
        migrations.AddConstraint(
            model_name="taxratesetting",
            constraint=models.UniqueConstraint(
                fields=("asset_name",),
                condition=models.Q(family__isnull=True, asset__isnull=True),
                name="unique_global_tax_rate_asset_name",
            ),
        ),
    ]
