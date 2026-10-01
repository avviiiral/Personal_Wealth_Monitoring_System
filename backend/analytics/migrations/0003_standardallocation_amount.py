from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("analytics", "0002_family_standardallocation"),
    ]

    operations = [
        migrations.AddField(
            model_name="standardallocation",
            name="allocation_amount",
            field=models.DecimalField(
                decimal_places=2,
                default=0,
                max_digits=20,
            ),
        ),
    ]
