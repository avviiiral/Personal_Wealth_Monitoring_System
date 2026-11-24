import django.db.models.deletion
from django.db import migrations, models
import django.utils.timezone


class Migration(migrations.Migration):
    dependencies = [
        ("mutual_funds", "0016_charliealpha_provider_choices"),
    ]

    operations = [
        migrations.CreateModel(
            name="MFProviderBenchmark",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("provider", models.CharField(default="CHARLIEALPHA", max_length=40)),
                ("slug", models.CharField(max_length=160)),
                ("name", models.CharField(blank=True, default="", max_length=300)),
                ("category", models.CharField(blank=True, default="", max_length=80)),
                ("source_url", models.URLField(blank=True, default="", max_length=1000)),
                ("fetched_at", models.DateTimeField(default=django.utils.timezone.now)),
            ],
            options={
                "db_table": "mf_provider_benchmarks",
                "ordering": ["provider", "category", "name"],
            },
        ),
        migrations.CreateModel(
            name="MFProviderBenchmarkObservation",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("observation_date", models.DateField(db_index=True)),
                ("normalized_value", models.DecimalField(decimal_places=8, max_digits=24)),
                ("normalization_method", models.CharField(default="REBASED_TO_100", max_length=30)),
                ("normalization_base_date", models.DateField()),
                ("normalization_base_value", models.DecimalField(decimal_places=8, default=100, max_digits=18)),
                ("source", models.CharField(default="CHARLIEALPHA_API", max_length=100)),
                ("source_url", models.URLField(blank=True, default="", max_length=1000)),
                ("fetched_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("benchmark", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="observations", to="mutual_funds.mfproviderbenchmark")),
            ],
            options={
                "db_table": "mf_provider_benchmark_observations",
                "ordering": ["benchmark", "normalization_base_date", "observation_date"],
            },
        ),
        migrations.AddConstraint(
            model_name="mfproviderbenchmark",
            constraint=models.UniqueConstraint(fields=("provider", "slug"), name="uq_mf_provider_benchmark_slug"),
        ),
        migrations.AddIndex(
            model_name="mfproviderbenchmark",
            index=models.Index(fields=["provider", "category"], name="mf_provider_bench_cat_idx"),
        ),
        migrations.AddConstraint(
            model_name="mfproviderbenchmarkobservation",
            constraint=models.UniqueConstraint(fields=("benchmark", "observation_date", "normalization_base_date"), name="uq_mf_prov_bench_obs_base"),
        ),
        migrations.AddIndex(
            model_name="mfproviderbenchmarkobservation",
            index=models.Index(fields=["benchmark", "normalization_base_date", "observation_date"], name="mf_prov_bench_obs_date_idx"),
        ),
    ]
