from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("investments", "0007_securitymaster_amc_name_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="securitymaster",
            name="peg_ratio",
            field=models.DecimalField(
                blank=True,
                decimal_places=2,
                max_digits=10,
                null=True,
            ),
        ),
    ]
