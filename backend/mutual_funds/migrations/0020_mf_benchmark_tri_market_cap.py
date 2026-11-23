# Generated for benchmark TRI and AMFI market-cap classifications.
import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("mutual_funds", "0019_mf_masterdata")]

    operations = [
        migrations.AddField(
            model_name="mfmasterdata",
            name="sector_allocation",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.CreateModel(
            name="MFBenchmarkTRI",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("index_code", models.CharField(db_index=True, max_length=100)),
                ("index_name", models.CharField(max_length=300)),
                ("date", models.DateField(db_index=True)),
                ("tri_value", models.DecimalField(decimal_places=4, max_digits=24)),
                ("source", models.CharField(max_length=100)),
                ("source_url", models.URLField(blank=True, default="", max_length=1000)),
                ("fetched_at", models.DateTimeField(default=django.utils.timezone.now)),
            ],
            options={
                "db_table": "mf_benchmark_tri",
                "ordering": ["index_code", "date"],
                "indexes": [models.Index(fields=["index_code", "-date"], name="mf_benchmark_tri_code_date_idx")],
                "constraints": [models.UniqueConstraint(fields=("index_code", "date"), name="unique_mf_benchmark_tri_date")],
            },
        ),
        migrations.CreateModel(
            name="MFMarketCapClassification",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("isin", models.CharField(db_index=True, max_length=20)),
                ("company_name", models.CharField(blank=True, default="", max_length=300)),
                ("market_cap_rank", models.PositiveIntegerField(blank=True, null=True)),
                ("market_cap_class", models.CharField(choices=[("LARGE_CAP", "Large Cap"), ("MID_CAP", "Mid Cap"), ("SMALL_CAP", "Small Cap")], max_length=20)),
                ("effective_period", models.CharField(max_length=20)),
                ("source", models.CharField(default="AMFI", max_length=100)),
                ("source_url", models.URLField(blank=True, default="", max_length=1000)),
                ("fetched_at", models.DateTimeField(default=django.utils.timezone.now)),
            ],
            options={
                "db_table": "mf_market_cap_classification",
                "ordering": ["effective_period", "market_cap_rank", "company_name"],
                "indexes": [models.Index(fields=["effective_period", "market_cap_class"], name="mf_market_cap_period_idx")],
                "constraints": [models.UniqueConstraint(fields=("isin", "effective_period"), name="unique_mf_market_cap_period_isin")],
            },
        ),
    ]
