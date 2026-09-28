from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("mutual_funds", "0011_family_scheme_code_constraint"),
    ]

    operations = [
        migrations.CreateModel(
            name="AMFIMasterScheme",
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
                ("scheme_code", models.CharField(max_length=50, unique=True)),
                ("scheme_name", models.CharField(max_length=300)),
                (
                    "isin_growth",
                    models.CharField(blank=True, max_length=30, null=True),
                ),
                (
                    "isin_dividend",
                    models.CharField(blank=True, max_length=30, null=True),
                ),
                ("is_active", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "ordering": ["scheme_name"],
                "indexes": [
                    models.Index(fields=["isin_growth"], name="mutual_fund_amb_7e5c7a_idx"),
                    models.Index(fields=["isin_dividend"], name="mutual_fund_amb_6b7d0e_idx"),
                ],
            },
        ),
        migrations.CreateModel(
            name="AMFIMasterNAV",
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
                ("date", models.DateField()),
                (
                    "nav",
                    models.DecimalField(decimal_places=6, max_digits=20),
                ),
                ("source", models.CharField(default="AMFI", max_length=50)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "scheme",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="nav_history",
                        to="mutual_funds.amfimasterscheme",
                    ),
                ),
            ],
            options={
                "ordering": ["-date"],
                "indexes": [
                    models.Index(fields=["scheme", "-date"], name="mutual_fund_amn_9b4e0a_idx"),
                    models.Index(fields=["date"], name="mutual_fund_amn_6f6a2c_idx"),
                ],
            },
        ),
        migrations.AddConstraint(
            model_name="amfimasternav",
            constraint=models.UniqueConstraint(
                fields=("scheme", "date", "source"),
                name="unique_amfi_master_nav",
            ),
        ),
    ]
