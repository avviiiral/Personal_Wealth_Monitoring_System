from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("portfolio", "0001_initial"),
        ("users", "0005_sync_tax_rate_state"),
    ]

    operations = [
        migrations.CreateModel(
            name="FamilyLiquidPosition",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("reporting_date", models.DateField()),
                ("document", models.JSONField(default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "family",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="liquid_positions",
                        to="users.familygroup",
                    ),
                ),
                (
                    "updated_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="updated_family_liquid_positions",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={"ordering": ["reporting_date", "family_id"]},
        ),
        migrations.AddConstraint(
            model_name="familyliquidposition",
            constraint=models.UniqueConstraint(
                fields=("family", "reporting_date"),
                name="uniq_fam_liquid_pos_date",
            ),
        ),
    ]
