from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("mutual_funds", "0012_amfi_master_data"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="mutualfundtransaction",
            index=models.Index(
                fields=["family", "family_name", "scheme", "transaction_date", "created_at", "id"],
                name="mf_tx_family_scheme_order_idx",
            ),
        ),
    ]
