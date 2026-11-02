# Generated for corporate-action exposure and reconciliation workflow.
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("corporate_actions", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="CorporateActionExposure",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("family_id_snapshot", models.PositiveBigIntegerField(blank=True, db_index=True, null=True)),
                ("exposure_type", models.CharField(choices=[("DIRECT", "Direct"), ("MUTUAL_FUND", "Mutual fund underlying"), ("PMS", "PMS underlying")], max_length=20)),
                ("investment_name", models.CharField(max_length=300)),
                ("investment_id", models.PositiveBigIntegerField(blank=True, null=True)),
                ("quantity", models.DecimalField(blank=True, decimal_places=8, max_digits=24, null=True)),
                ("holding_weight", models.DecimalField(blank=True, decimal_places=6, max_digits=12, null=True)),
                ("snapshot_date", models.DateField(blank=True, null=True)),
                ("is_current", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("action", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="exposures", to="corporate_actions.corporateaction")),
                ("owner", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="corporate_action_exposures", to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.CreateModel(
            name="CorporateActionReconciliation",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("family_id_snapshot", models.PositiveBigIntegerField(blank=True, db_index=True, null=True)),
                ("status", models.CharField(choices=[("PENDING", "Pending review"), ("ALREADY_RECORDED", "Already recorded"), ("COMPLETED", "Completed"), ("DISMISSED", "Dismissed"), ("MANUAL_REVIEW", "Manual verification required")], default="PENDING", max_length=24)),
                ("matched_transaction_ids", models.JSONField(blank=True, default=list)),
                ("suggested_quantity", models.DecimalField(blank=True, decimal_places=8, max_digits=24, null=True)),
                ("reviewed_at", models.DateTimeField(blank=True, null=True)),
                ("notes", models.TextField(blank=True, default="")),
                ("idempotency_key", models.CharField(max_length=64, unique=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("action", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="reconciliations", to="corporate_actions.corporateaction")),
                ("owner", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="corporate_action_reconciliations", to=settings.AUTH_USER_MODEL)),
                ("reviewed_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="reviewed_corporate_action_reconciliations", to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.CreateModel(
            name="CorporateActionNotification",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("reviewed", models.BooleanField(default=False)),
                ("dismissed", models.BooleanField(default=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("action", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="notifications", to="corporate_actions.corporateaction")),
                ("owner", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="corporate_action_notifications", to=settings.AUTH_USER_MODEL)),
                ("reconciliation", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="notifications", to="corporate_actions.corporateactionreconciliation")),
            ],
        ),
        migrations.AddConstraint(model_name="corporateactionexposure", constraint=models.UniqueConstraint(fields=("action", "owner", "exposure_type", "investment_name", "investment_id"), name="uniq_corp_action_exposure")),
        migrations.AddConstraint(model_name="corporateactionreconciliation", constraint=models.UniqueConstraint(fields=("action", "owner"), name="uniq_corp_action_reconciliation")),
        migrations.AddConstraint(model_name="corporateactionnotification", constraint=models.UniqueConstraint(fields=("action", "owner"), name="uniq_corp_action_notification")),
        migrations.AddIndex(model_name="corporateactionexposure", index=models.Index(fields=["owner", "action"], name="corp_exposure_owner_idx")),
        migrations.AddIndex(model_name="corporateactionexposure", index=models.Index(fields=["family_id_snapshot", "owner"], name="corp_exposure_family_idx")),
        migrations.AddIndex(model_name="corporateactionreconciliation", index=models.Index(fields=["owner", "status", "-updated_at"], name="corp_recon_owner_status_idx")),
        migrations.AddIndex(model_name="corporateactionnotification", index=models.Index(fields=["owner", "reviewed", "dismissed", "-created_at"], name="corp_notif_inbox_idx")),
    ]
