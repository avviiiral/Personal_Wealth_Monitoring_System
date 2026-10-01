from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("investments", "0104_family_transaction_order_index"),
        ("users", "0003_family_tax_rate_settings"),
    ]

    operations = [
        migrations.CreateModel(
            name="TaxRateChangeLog",
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
                ("username", models.CharField(max_length=150)),
                ("family_name", models.CharField(blank=True, default="", max_length=100)),
                ("asset_name", models.CharField(max_length=255)),
                ("changed_at", models.DateTimeField(auto_now_add=True)),
                ("change_from", models.JSONField(default=dict)),
                ("change_to", models.JSONField(default=dict)),
                (
                    "asset",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="tax_rate_change_logs",
                        to="investments.asset",
                    ),
                ),
                (
                    "family",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="tax_rate_change_logs",
                        to="users.familygroup",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="tax_rate_change_logs",
                        to="auth.user",
                    ),
                ),
            ],
            options={
                "ordering": ["-changed_at", "-id"],
            },
        ),
    ]
