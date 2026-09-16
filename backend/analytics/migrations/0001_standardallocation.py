from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="StandardAllocation",
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
                ("family_name", models.CharField(blank=True, default="", max_length=255)),
                ("asset_category", models.CharField(max_length=100)),
                (
                    "allocation_percent",
                    models.DecimalField(decimal_places=2, default=0, max_digits=6),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=models.deletion.CASCADE,
                        related_name="standard_allocations",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ["asset_category"],
            },
        ),
        migrations.AddConstraint(
            model_name="standardallocation",
            constraint=models.UniqueConstraint(
                fields=("user", "family_name", "asset_category"),
                name="unique_standard_allocation_scope",
            ),
        ),
    ]
