from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("analytics", "0007_standardallocation_family_member_label"),
    ]

    operations = [
        migrations.AlterField(
            model_name="standardallocation",
            name="allocation_percent",
            field=models.DecimalField(max_digits=8, decimal_places=4, default=0),
        ),
        migrations.AlterField(
            model_name="standardallocation",
            name="allocation_amount",
            field=models.DecimalField(max_digits=20, decimal_places=4, default=0),
        ),
    ]
