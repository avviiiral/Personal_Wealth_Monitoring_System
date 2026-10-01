from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("users", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="TaxRateSetting",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("asset_name", models.CharField(max_length=255)),
                ("tax_rate", models.DecimalField(decimal_places=4, default=0, max_digits=7)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=models.deletion.CASCADE,
                        related_name="tax_rate_settings",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ["asset_name"],
            },
        ),
        migrations.AddConstraint(
            model_name="taxratesetting",
            constraint=models.UniqueConstraint(
                fields=("user", "asset_name"),
                name="unique_user_tax_rate_asset_name",
            ),
        ),
    ]
