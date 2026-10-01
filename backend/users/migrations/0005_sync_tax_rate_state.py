from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("users", "0004_tax_rate_change_log"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.AlterModelOptions(
                    name="taxratesetting",
                    options={"ordering": ["asset__name"]},
                ),
                migrations.RemoveConstraint(
                    model_name="taxratesetting",
                    name="unique_user_tax_rate_asset_name",
                ),
            ],
            database_operations=[],
        ),
    ]
