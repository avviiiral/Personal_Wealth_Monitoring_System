from django.db import migrations, models


def copy_existing_benchmarks_to_comparison(apps, schema_editor):
    InvestmentProduct = apps.get_model("watchlist", "InvestmentProduct")
    MutualFundProduct = apps.get_model("watchlist", "MutualFundProduct")
    PMSProduct = apps.get_model("watchlist", "PMSProduct")
    db_alias = schema_editor.connection.alias

    for fund in MutualFundProduct.objects.using(db_alias).iterator():
        if fund.benchmark:
            InvestmentProduct.objects.using(db_alias).filter(pk=fund.product_id).update(
                comparison_benchmark=fund.benchmark
            )
    for pms in PMSProduct.objects.using(db_alias).iterator():
        if pms.benchmark:
            InvestmentProduct.objects.using(db_alias).filter(pk=pms.product_id).update(
                comparison_benchmark=pms.benchmark
            )


class Migration(migrations.Migration):
    dependencies = [("watchlist", "0008_fund_manager_lookup_cache")]

    operations = [
        migrations.AddField(
            model_name="investmentproduct",
            name="comparison_benchmark",
            field=models.CharField(blank=True, max_length=300, null=True),
        ),
        migrations.RunPython(copy_existing_benchmarks_to_comparison, migrations.RunPython.noop),
    ]
