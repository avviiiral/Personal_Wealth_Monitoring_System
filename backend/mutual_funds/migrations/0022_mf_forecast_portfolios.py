# Add MF Forecast snapshots and distinguish provider retrieval dates from disclosures.
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):
    dependencies = [("mutual_funds", "0021_mf_rolling_3y_cagr")]

    operations = [
        migrations.AddField(
            model_name="mfmasterholding",
            name="ticker",
            field=models.CharField(blank=True, default="", max_length=40),
        ),
        migrations.AddField(
            model_name="mfmasterholding",
            name="portfolio_date_basis",
            field=models.CharField(
                choices=[
                    ("DISCLOSURE", "Provider disclosure date"),
                    ("FETCHED_AT", "Provider retrieval date only"),
                ],
                db_index=True,
                default="DISCLOSURE",
                max_length=16,
            ),
        ),
        migrations.RemoveConstraint(
            model_name="mfmasterholding",
            name="unique_mf_master_holding_snapshot",
        ),
        migrations.AddConstraint(
            model_name="mfmasterholding",
            constraint=models.UniqueConstraint(
                fields=("scheme", "portfolio_date", "portfolio_date_basis", "security_key"),
                name="uq_mf_holding_date_basis",
            ),
        ),
        migrations.CreateModel(
            name="MFExternalPortfolioSnapshot",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("provider", models.CharField(choices=[("MFFORECAST", "MF Forecast")], default="MFFORECAST", max_length=40)),
                ("observed_at", models.DateTimeField(db_index=True, default=django.utils.timezone.now)),
                ("provider_as_of_date", models.DateField(blank=True, null=True)),
                ("scheme_name", models.CharField(blank=True, default="", max_length=300)),
                ("fund_house", models.CharField(blank=True, default="", max_length=200)),
                ("category", models.CharField(blank=True, default="", max_length=160)),
                ("aum_cr", models.DecimalField(blank=True, decimal_places=4, max_digits=24, null=True)),
                ("expense_ratio_pct", models.DecimalField(blank=True, decimal_places=4, max_digits=12, null=True)),
                ("asset_allocation", models.JSONField(blank=True, default=dict)),
                ("holdings_count", models.PositiveIntegerField(default=0)),
                ("total_weight_pct", models.DecimalField(blank=True, decimal_places=4, max_digits=12, null=True)),
                ("quality_flags", models.JSONField(blank=True, default=list)),
                ("source_url", models.URLField(blank=True, default="", max_length=1000)),
                ("raw_file_path", models.CharField(blank=True, default="", max_length=1000)),
                ("scheme", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="external_portfolio_snapshots", to="mutual_funds.amfimasterscheme")),
            ],
            options={
                "db_table": "mf_external_portfolio_snapshots",
                "ordering": ["-observed_at"],
                "indexes": [
                    models.Index(fields=["scheme", "-observed_at"], name="mf_ext_port_scheme_obs_idx"),
                    models.Index(fields=["provider", "-observed_at"], name="mf_ext_port_prov_obs_idx"),
                ],
            },
        ),
    ]
