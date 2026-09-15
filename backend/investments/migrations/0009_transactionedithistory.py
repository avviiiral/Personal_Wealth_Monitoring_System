from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("investments", "0008_securitymaster_peg_ratio"),
    ]

    operations = [
        migrations.CreateModel(
            name="TransactionEditHistory",
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
                ("edited_at", models.DateTimeField(auto_now_add=True)),
                ("old_values", models.JSONField(default=dict)),
                ("new_values", models.JSONField(default=dict)),
                ("changed_fields", models.JSONField(default=list)),
                (
                    "edited_by",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="transaction_edits_made",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "owner",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="transaction_edit_history",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "transaction",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="edit_history",
                        to="investments.transaction",
                    ),
                ),
            ],
            options={
                "ordering": ["-edited_at"],
            },
        ),
        migrations.AddIndex(
            model_name="transactionedithistory",
            index=models.Index(
                fields=["owner", "-edited_at"],
                name="tx_edit_owner_date_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="transactionedithistory",
            index=models.Index(
                fields=["transaction", "-edited_at"],
                name="tx_edit_transaction_date_idx",
            ),
        ),
    ]
