# User-approved fallback identity matching for missing-source-ISIN actions.
import django.db.models.deletion
from django.db import migrations, models

class Migration(migrations.Migration):
    dependencies = [
        ("corporate_actions", "0002_reconciliation_notifications"),
        ("investments", "0001_initial"),
    ]
    operations = [
        migrations.AddField(
            model_name="corporateactionreconciliation",
            name="identity_match_method",
            field=models.CharField(blank=True, default="", max_length=24),
        ),
        migrations.AddField(
            model_name="corporateactionreconciliation",
            name="candidate_asset_ids",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name="corporateactionreconciliation",
            name="selected_asset",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="corporate_action_reconciliations", to="investments.asset"),
        ),
        migrations.AlterField(
            model_name="corporateactionreconciliation",
            name="status",
            field=models.CharField(choices=[
                ("PENDING", "Pending review"),
                ("ALREADY_RECORDED", "Already recorded"),
                ("COMPLETED", "Completed"),
                ("DISMISSED", "Dismissed"),
                ("MANUAL_REVIEW", "Manual verification required"),
                ("PENDING_USER_APPROVAL", "Awaiting identity approval"),
                ("APPROVED", "Approved"),
                ("REJECTED", "Rejected"),
            ], default="PENDING", max_length=24),
        ),
    ]
