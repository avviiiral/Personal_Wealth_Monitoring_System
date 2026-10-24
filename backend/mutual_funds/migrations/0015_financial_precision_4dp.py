from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("mutual_funds", "0014_rename_mutual_fund_amn_9b4e0a_idx_mutual_fund_scheme__e64ace_idx_and_more"),
    ]

    operations = [
        migrations.AlterField(
            model_name="mutualfundtransaction",
            name="amount",
            field=models.DecimalField(max_digits=20, decimal_places=4),
        ),
        migrations.AlterField(
            model_name="mutualfundtransaction",
            name="fees",
            field=models.DecimalField(max_digits=20, decimal_places=4, default=0),
        ),
        migrations.AlterField(
            model_name="sip",
            name="amount",
            field=models.DecimalField(max_digits=20, decimal_places=4),
        ),
        migrations.AlterField(
            model_name="mutualfundholding",
            name="invested_value",
            field=models.DecimalField(max_digits=20, decimal_places=4, default=0),
        ),
        migrations.AlterField(
            model_name="mutualfundholding",
            name="current_value",
            field=models.DecimalField(max_digits=20, decimal_places=4, default=0),
        ),
        migrations.AlterField(
            model_name="mutualfundholding",
            name="unrealized_pnl",
            field=models.DecimalField(max_digits=20, decimal_places=4, default=0),
        ),
        migrations.AlterField(
            model_name="sipinstallment",
            name="amount",
            field=models.DecimalField(max_digits=20, decimal_places=4),
        ),
    ]
