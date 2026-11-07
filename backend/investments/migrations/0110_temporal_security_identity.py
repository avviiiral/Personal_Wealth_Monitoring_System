from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("investments", "0109_alter_securityidentityresolutionlog_match_method"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]
    operations = [
        migrations.CreateModel(
            name="SecurityIdentifierMapping",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("identifier_type", models.CharField(max_length=24, choices=[("ISIN", "ISIN"), ("NSE_SYMBOL", "NSE symbol"), ("BSE_SYMBOL", "BSE symbol"), ("DEPOSITORY_ISIN", "Depository ISIN")])),
                ("identifier_value", models.CharField(max_length=100)),
                ("exchange", models.CharField(max_length=16, blank=True, default="")),
                ("valid_from", models.DateField(null=True, blank=True)),
                ("valid_to", models.DateField(null=True, blank=True)),
                ("source", models.CharField(max_length=80)),
                ("source_reference", models.URLField(max_length=1500)),
                ("source_document_hash", models.CharField(max_length=64, blank=True, default="")),
                ("discovered_at", models.DateTimeField(auto_now_add=True)),
                ("verification_status", models.CharField(max_length=16, choices=[("PENDING", "Pending"), ("VERIFIED", "Verified"), ("REJECTED", "Rejected"), ("CONFLICT", "Conflict")], default="PENDING")),
                ("confidence", models.DecimalField(max_digits=5, decimal_places=4, default=0)),
                ("evidence", models.JSONField(default=dict, blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("asset", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="identifier_mappings", to="investments.asset")),
                ("security_master", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="identifier_mappings", to="investments.securitymaster")),
                ("discovered_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="discovered_security_identifiers", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "ordering": ["identifier_type", "identifier_value"],
                "indexes": [
                    models.Index(fields=["identifier_type", "identifier_value"], name="sec_ident_lookup_idx"),
                    models.Index(fields=["asset", "valid_from", "valid_to"], name="sec_ident_asset_dates_idx"),
                    models.Index(fields=["verification_status", "discovered_at"], name="sec_ident_review_idx"),
                    models.Index(fields=["exchange", "identifier_type", "identifier_value"], name="sec_ident_exchange_idx"),
                ],
            },
        ),
        migrations.AddConstraint(
            model_name="securityidentifiermapping",
            constraint=models.UniqueConstraint(fields=("asset", "identifier_type", "identifier_value", "valid_from", "source"), name="sec_ident_asset_source_uniq"),
        ),
        migrations.AddConstraint(
            model_name="securityidentifiermapping",
            constraint=models.CheckConstraint(condition=models.Q(valid_to__isnull=True) | models.Q(valid_from__isnull=True) | models.Q(valid_to__gte=models.F("valid_from")), name="sec_ident_dates_ordered"),
        ),
        migrations.CreateModel(
            name="SecurityIdentityDiscoveryCheckpoint",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("provider", models.CharField(max_length=80)),
                ("scope", models.CharField(max_length=255, default="global")),
                ("last_success_at", models.DateTimeField(null=True, blank=True)),
                ("last_window_start", models.DateField(null=True, blank=True)),
                ("last_window_end", models.DateField(null=True, blank=True)),
                ("last_error", models.TextField(blank=True, default="")),
                ("metadata", models.JSONField(default=dict, blank=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"constraints": [models.UniqueConstraint(fields=("provider", "scope"), name="sec_ident_checkpoint_uniq")]},
        ),
    ]
