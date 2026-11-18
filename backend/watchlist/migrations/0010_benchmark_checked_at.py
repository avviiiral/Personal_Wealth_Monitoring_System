from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("watchlist", "0009_comparison_benchmark"),
    ]

    operations = [
        migrations.AddField(
            model_name="mutualfundproduct",
            name="benchmark_checked_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="pmsproduct",
            name="benchmark_checked_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
