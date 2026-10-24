from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("investments", "0106_assetunderlyingholding_upload_audit"),
    ]

    operations = [
        migrations.AlterField(
            model_name="transaction",
            name="amount",
            field=models.DecimalField(max_digits=20, decimal_places=4),
        ),
        migrations.AlterField(
            model_name="transaction",
            name="fees",
            field=models.DecimalField(max_digits=20, decimal_places=4, default=0),
        ),
        migrations.AlterField(
            model_name="holding",
            name="invested_value",
            field=models.DecimalField(max_digits=20, decimal_places=4, default=0),
        ),
        migrations.AlterField(
            model_name="holding",
            name="current_value",
            field=models.DecimalField(max_digits=20, decimal_places=4, default=0),
        ),
        migrations.AlterField(
            model_name="holding",
            name="unrealized_pnl",
            field=models.DecimalField(max_digits=20, decimal_places=4, default=0),
        ),
        migrations.AlterField(
            model_name="portfolioposition",
            name="invested_value",
            field=models.DecimalField(max_digits=20, decimal_places=4, default=0),
        ),
        migrations.AlterField(
            model_name="portfolioposition",
            name="current_value",
            field=models.DecimalField(max_digits=20, decimal_places=4, default=0),
        ),
        migrations.AlterField(
            model_name="portfolioposition",
            name="gain",
            field=models.DecimalField(max_digits=20, decimal_places=4, default=0),
        ),
    ]
