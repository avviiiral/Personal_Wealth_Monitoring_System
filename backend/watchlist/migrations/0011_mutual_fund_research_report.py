from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("watchlist", "0010_benchmark_checked_at"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="MutualFundResearchReport",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("report_key", models.CharField(max_length=100)),
                ("report_date", models.DateField()),
                ("report_cycle", models.CharField(blank=True, max_length=100)),
                ("source_file", models.CharField(blank=True, max_length=255)),
                ("payload", models.JSONField(default=dict)),
                ("imported_at", models.DateTimeField(auto_now=True)),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["-report_date", "-imported_at"]},
        ),
        migrations.AddConstraint(
            model_name="mutualfundresearchreport",
            constraint=models.UniqueConstraint(fields=("report_key", "report_date"), name="unique_mf_research_report_key_date"),
        ),
    ]
