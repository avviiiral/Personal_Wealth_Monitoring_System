import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("mutual_funds", "0025_charliealpha_benchmark_history"),
    ]

    operations = [
        migrations.CreateModel(
            name="MFProviderResourceSnapshot",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("family", models.CharField(
                    choices=[
                        ("NPS", "National Pension System"),
                        ("SIF", "Specialized Investment Funds"),
                        ("GIFT", "GIFT City / IFSC Funds"),
                        ("INSTRUMENTS", "Instruments"),
                        ("CORPORATE_ACTIONS", "Corporate Actions"),
                    ],
                    db_index=True,
                    max_length=24,
                )),
                ("endpoint", models.CharField(max_length=160)),
                ("identifier", models.CharField(blank=True, default="", max_length=200)),
                ("request_params", models.JSONField(blank=True, default=dict)),
                ("payload", models.JSONField(blank=True, default=dict)),
                ("records", models.JSONField(blank=True, default=list)),
                ("record_count", models.PositiveIntegerField(default=0)),
                ("as_of_date", models.DateField(blank=True, null=True)),
                ("source_url", models.URLField(blank=True, default="", max_length=1000)),
                ("payload_sha256", models.CharField(blank=True, db_index=True, default="", max_length=64)),
                ("success", models.BooleanField(default=True)),
                ("error", models.TextField(blank=True, default="")),
                ("fetched_at", models.DateTimeField(db_index=True, default=django.utils.timezone.now)),
            ],
            options={
                "db_table": "mf_provider_resource_snapshots",
                "ordering": ["-fetched_at", "-id"],
            },
        ),
        migrations.AddIndex(
            model_name="mfproviderresourcesnapshot",
            index=models.Index(
                fields=["family", "endpoint", "-fetched_at"],
                name="mf_pr_resource_fam_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="mfproviderresourcesnapshot",
            index=models.Index(
                fields=["family", "identifier", "-fetched_at"],
                name="mf_pr_resource_ident_idx",
            ),
        ),
    ]
