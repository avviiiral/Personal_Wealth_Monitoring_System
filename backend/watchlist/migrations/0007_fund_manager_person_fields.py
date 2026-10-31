# Add person-level fund manager attribution to mutual fund and PMS products.
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("watchlist", "0006_rename_watchlist_benchmark_date_idx_watchlist_b_benchma_1e4024_idx"),
    ]

    operations = [
        migrations.AddField(
            model_name="mutualfundproduct",
            name="fund_manager_website",
            field=models.URLField(blank=True, max_length=1000, null=True),
        ),
        migrations.AddField(
            model_name="pmsproduct",
            name="fund_manager",
            field=models.CharField(blank=True, max_length=500, null=True),
        ),
        migrations.AddField(
            model_name="pmsproduct",
            name="fund_manager_website",
            field=models.URLField(blank=True, max_length=1000, null=True),
        ),
    ]
