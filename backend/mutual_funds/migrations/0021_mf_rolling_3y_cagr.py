# Generated for rolling three-year NAV return statistics.
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("mutual_funds", "0020_mf_benchmark_tri_market_cap")]

    operations = [
        migrations.AddField(
            model_name="mfmasterdata",
            name="rolling_3y_cagr_mean_pct",
            field=models.DecimalField(blank=True, decimal_places=4, max_digits=14, null=True),
        ),
        migrations.AddField(
            model_name="mfmasterdata",
            name="rolling_3y_cagr_min_pct",
            field=models.DecimalField(blank=True, decimal_places=4, max_digits=14, null=True),
        ),
        migrations.AddField(
            model_name="mfmasterdata",
            name="rolling_3y_cagr_max_pct",
            field=models.DecimalField(blank=True, decimal_places=4, max_digits=14, null=True),
        ),
        migrations.AddField(
            model_name="mfmasterdata",
            name="rolling_3y_cagr_observations",
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
    ]
