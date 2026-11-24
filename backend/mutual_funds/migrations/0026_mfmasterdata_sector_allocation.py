from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("mutual_funds", "0025_charliealpha_benchmark_history"),
    ]

    operations = [
        migrations.AddField(
            model_name="mfmasterdata",
            name="sector_allocation",
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
