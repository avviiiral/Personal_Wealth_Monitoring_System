import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models
import django.utils.timezone


class Migration(migrations.Migration):
    dependencies = [
        ("investments", "0107_financial_precision_4dp"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("users", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="SecurityAlias",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("alias", models.CharField(max_length=255)),
                ("normalized_alias", models.CharField(editable=False, max_length=255)),
                ("approved_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("is_active", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("approved_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="approved_security_aliases", to=settings.AUTH_USER_MODEL)),
                ("asset", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="approved_security_aliases", to="investments.asset")),
                ("family", models.ForeignKey(db_index=True, on_delete=django.db.models.deletion.PROTECT, related_name="security_aliases", to="users.familygroup")),
            ],
            options={"ordering": ["normalized_alias"]},
        ),
        migrations.AddConstraint(
            model_name="securityalias",
            constraint=models.UniqueConstraint(fields=("family", "normalized_alias"), name="sec_alias_family_norm_uniq"),
        ),
        migrations.AddIndex(
            model_name="securityalias",
            index=models.Index(fields=["family", "is_active"], name="sec_alias_family_active_idx"),
        ),
        migrations.CreateModel(
            name="SecurityIdentityResolutionLog",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("row_number", models.PositiveIntegerField()),
                ("input_asset_name", models.CharField(blank=True, default="", max_length=255)),
                ("input_underlying", models.CharField(blank=True, default="", max_length=255)),
                ("input_isin", models.CharField(blank=True, default="", max_length=20)),
                ("canonical_name", models.CharField(blank=True, default="", max_length=255)),
                ("match_method", models.CharField(choices=[("ISIN", "ISIN"), ("APPROVED_ALIAS", "Approved alias"), ("NORMALIZED_NAME", "Normalized name"), ("NONE", "No match")], default="NONE", max_length=20)),
                ("status", models.CharField(choices=[("MATCHED", "Matched"), ("UNRESOLVED", "Unresolved"), ("AMBIGUOUS", "Ambiguous"), ("CONFLICT", "Conflict")], max_length=20)),
                ("details", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("actor", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="security_identity_resolution_logs", to=settings.AUTH_USER_MODEL)),
                ("family", models.ForeignKey(db_index=True, on_delete=django.db.models.deletion.PROTECT, related_name="security_identity_resolution_logs", to="users.familygroup")),
                ("resolved_asset", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="security_identity_resolution_logs", to="investments.asset")),
                ("upload", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="security_identity_resolution_logs", to="investments.transactionupload")),
            ],
            options={"ordering": ["-created_at", "-id"]},
        ),
        migrations.AddIndex(
            model_name="securityidentityresolutionlog",
            index=models.Index(fields=["family", "-created_at"], name="sec_res_family_date_idx"),
        ),
        migrations.AddIndex(
            model_name="securityidentityresolutionlog",
            index=models.Index(fields=["upload", "row_number"], name="sec_res_upload_row_idx"),
        ),
    ]
