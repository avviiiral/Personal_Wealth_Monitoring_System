from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("portfolio_news", "0012_portfolionewsmatch_family"),
    ]

    operations = [
        migrations.AddField(
            model_name="portfolionewsalert",
            name="connection_type",
            field=models.CharField(
                choices=[
                    ("direct", "Direct asset"),
                    ("underlying", "Underlying holding"),
                ],
                default="direct",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="portfolionewsalert",
            name="underlying_name",
            field=models.CharField(blank=True, max_length=300),
        ),
        migrations.AddField(
            model_name="portfolionewsalert",
            name="underlying_weight",
            field=models.DecimalField(
                blank=True,
                decimal_places=4,
                max_digits=10,
                null=True,
            ),
        ),
    ]
