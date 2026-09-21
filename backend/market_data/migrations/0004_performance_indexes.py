from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("market_data", "0003_marketprice_updated_by"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="marketprice",
            index=models.Index(
                fields=["source", "date"],
                name="marketprice_source_date_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="marketprice",
            index=models.Index(
                fields=["asset", "source", "-date"],
                name="marketprice_asset_src_date_idx",
            ),
        ),
    ]
