from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("investments", "0103_assetunderlyingholding_isin"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="transaction",
            index=models.Index(
                fields=["family", "family_name", "asset", "transaction_date", "created_at", "id"],
                name="transaction_family_asset_order_idx",
            ),
        ),
    ]
