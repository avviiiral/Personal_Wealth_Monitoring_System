from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True
    operations = [
        migrations.CreateModel(
            name="InvestmentProduct",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("product_type", models.CharField(choices=[("MUTUAL_FUND", "Mutual Fund"), ("PMS", "PMS")], max_length=20)),
                ("name", models.CharField(max_length=500)), ("provider", models.CharField(blank=True, max_length=300, null=True)),
                ("country", models.CharField(blank=True, max_length=100, null=True)), ("category", models.CharField(blank=True, max_length=200, null=True)),
                ("sub_category", models.CharField(blank=True, max_length=200, null=True)), ("isin", models.CharField(blank=True, max_length=30, null=True)),
                ("external_identifier", models.CharField(blank=True, max_length=150, null=True)), ("currency", models.CharField(blank=True, max_length=20, null=True)),
                ("source", models.CharField(blank=True, max_length=100, null=True)), ("source_reference", models.CharField(blank=True, max_length=1000, null=True)),
                ("source_date", models.DateField(blank=True, null=True)), ("official_website", models.URLField(blank=True, max_length=1000, null=True)),
                ("is_active", models.BooleanField(default=True)), ("identity_key", models.CharField(max_length=600, unique=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)), ("updated_at", models.DateTimeField(auto_now=True)),
            ], options={"ordering": ["name"]},
        ),
        migrations.CreateModel(
            name="MutualFundProduct",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("scheme_code", models.CharField(blank=True, max_length=50, null=True)), ("fund_type", models.CharField(blank=True, max_length=150, null=True)),
                ("plan", models.CharField(blank=True, max_length=50, null=True)), ("option", models.CharField(blank=True, max_length=100, null=True)),
                ("inception_date", models.DateField(blank=True, null=True)), ("aum", models.DecimalField(blank=True, decimal_places=2, max_digits=24, null=True)),
                ("expense_ratio", models.DecimalField(blank=True, decimal_places=4, max_digits=10, null=True)), ("benchmark", models.CharField(blank=True, max_length=300, null=True)),
                ("fund_manager", models.CharField(blank=True, max_length=500, null=True)), ("risk_level", models.CharField(blank=True, max_length=100, null=True)),
                ("minimum_investment", models.DecimalField(blank=True, decimal_places=2, max_digits=24, null=True)), ("exit_load", models.CharField(blank=True, max_length=500, null=True)),
                ("latest_nav", models.DecimalField(blank=True, decimal_places=8, max_digits=24, null=True)), ("latest_nav_date", models.DateField(blank=True, null=True)),
                ("product", models.OneToOneField(on_delete=models.deletion.CASCADE, related_name="mutual_fund", to="watchlist.investmentproduct")),
            ],
        ),
        migrations.CreateModel(
            name="PMSProduct",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("strategy_name", models.CharField(blank=True, max_length=500, null=True)), ("strategy_type", models.CharField(blank=True, max_length=200, null=True)),
                ("asset_class", models.CharField(blank=True, max_length=200, null=True)), ("benchmark", models.CharField(blank=True, max_length=300, null=True)),
                ("aum", models.DecimalField(blank=True, decimal_places=2, max_digits=24, null=True)), ("inception_date", models.DateField(blank=True, null=True)),
                ("minimum_investment", models.DecimalField(blank=True, decimal_places=2, max_digits=24, null=True)), ("latest_value", models.DecimalField(blank=True, decimal_places=8, max_digits=24, null=True)),
                ("risk_information", models.CharField(blank=True, max_length=500, null=True)),
                ("product", models.OneToOneField(on_delete=models.deletion.CASCADE, related_name="pms", to="watchlist.investmentproduct")),
            ],
        ),
        migrations.CreateModel(
            name="PerformanceSnapshot",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")), ("date", models.DateField()),
                ("nav_or_value", models.DecimalField(blank=True, decimal_places=8, max_digits=24, null=True)), ("aum", models.DecimalField(blank=True, decimal_places=2, max_digits=24, null=True)),
                ("return_1d", models.DecimalField(blank=True, decimal_places=6, max_digits=12, null=True)), ("return_1w", models.DecimalField(blank=True, decimal_places=6, max_digits=12, null=True)),
                ("return_1m", models.DecimalField(blank=True, decimal_places=6, max_digits=12, null=True)), ("return_3m", models.DecimalField(blank=True, decimal_places=6, max_digits=12, null=True)),
                ("return_6m", models.DecimalField(blank=True, decimal_places=6, max_digits=12, null=True)), ("return_1y", models.DecimalField(blank=True, decimal_places=6, max_digits=12, null=True)),
                ("return_3y", models.DecimalField(blank=True, decimal_places=6, max_digits=12, null=True)), ("return_5y", models.DecimalField(blank=True, decimal_places=6, max_digits=12, null=True)),
                ("return_since_inception", models.DecimalField(blank=True, decimal_places=6, max_digits=12, null=True)), ("cagr", models.DecimalField(blank=True, decimal_places=6, max_digits=12, null=True)),
                ("source", models.CharField(max_length=100)), ("source_reference", models.CharField(blank=True, max_length=1000, null=True)), ("fetched_at", models.DateTimeField(auto_now_add=True)),
                ("product", models.ForeignKey(on_delete=models.deletion.CASCADE, related_name="performance_snapshots", to="watchlist.investmentproduct")),
            ], options={"ordering": ["-date"]},
        ),
        migrations.CreateModel(
            name="DiscoveryRun",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")), ("source", models.CharField(max_length=100)),
                ("started_at", models.DateTimeField(auto_now_add=True)), ("finished_at", models.DateTimeField(blank=True, null=True)),
                ("discovered", models.PositiveIntegerField(default=0)), ("updated", models.PositiveIntegerField(default=0)), ("failed", models.PositiveIntegerField(default=0)),
                ("details", models.JSONField(default=dict)),
            ], options={"ordering": ["-started_at"]},
        ),
        migrations.AddIndex(model_name="investmentproduct", index=models.Index(fields=["product_type", "name"], name="watchlist_investment_product__f4f1d5_idx")),
        migrations.AddIndex(model_name="investmentproduct", index=models.Index(fields=["product_type", "provider"], name="watchlist_investment_product__b6f0a3_idx")),
        migrations.AddIndex(model_name="investmentproduct", index=models.Index(fields=["isin"], name="watchlist_investment_isin_8f6b9b_idx")),
        migrations.AddIndex(model_name="investmentproduct", index=models.Index(fields=["external_identifier"], name="watchlist_investment_external_8f1a23_idx")),
        migrations.AddConstraint(model_name="performancesnapshot", constraint=models.UniqueConstraint(fields=("product", "date", "source"), name="unique_watchlist_performance_snapshot")),
        migrations.AddIndex(model_name="performancesnapshot", index=models.Index(fields=["product", "-date"], name="watchlist_performance_product_date_idx")),
        migrations.AddIndex(model_name="performancesnapshot", index=models.Index(fields=["date"], name="watchlist_performance_date_idx")),
    ]
