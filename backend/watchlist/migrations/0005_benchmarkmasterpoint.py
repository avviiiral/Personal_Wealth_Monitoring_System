from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("watchlist", "0004_watchlistentry"),
    ]

    operations = [
        migrations.CreateModel(
            name="BenchmarkMasterPoint",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("benchmark", models.CharField(max_length=100)),
                ("date", models.DateField()),
                ("value", models.DecimalField(decimal_places=8, max_digits=24)),
                ("source", models.CharField(max_length=100)),
            ],
            options={
                "ordering": ["date"],
                "indexes": [
                    models.Index(
                        fields=["benchmark", "date"],
                        name="watchlist_benchmark_date_idx",
                    ),
                ],
                "constraints": [
                    models.UniqueConstraint(
                        fields=["benchmark", "date", "source"],
                        name="unique_watchlist_benchmark_master_point",
                    ),
                ],
            },
        ),
    ]
