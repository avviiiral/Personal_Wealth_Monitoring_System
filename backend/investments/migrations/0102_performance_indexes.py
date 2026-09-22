from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("investments", "0101_family_transaction_idempotency_schema"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="transaction",
            index=models.Index(
                fields=["family", "asset", "transaction_date"],
                name="transaction_asset_date_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="transaction",
            index=models.Index(
                fields=["family", "transaction_date"],
                name="transaction_family_date_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="assetunderlyingholding",
            index=models.Index(
                fields=["family", "stock_name"],
                name="au_family_stock_idx",
            ),
        ),
    ]
