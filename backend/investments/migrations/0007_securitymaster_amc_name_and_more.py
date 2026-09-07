from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('investments', '0006_transaction_advisors_transaction_asset_class_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='securitymaster',
            name='amc_name',
            field=models.CharField(blank=True, max_length=255, null=True),
        ),
        migrations.AddField(
            model_name='securitymaster',
            name='pe_ratio',
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True),
        ),
        migrations.AddField(
            model_name='securitymaster',
            name='pb_ratio',
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True),
        ),
        migrations.AddField(
            model_name='securitymaster',
            name='roe',
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True),
        ),
        migrations.AddField(
            model_name='securitymaster',
            name='credit_rating',
            field=models.CharField(
                blank=True,
                choices=[
                    ('SOVEREIGN', 'Sovereign'),
                    ('AAA', 'AAA / AAA+'),
                    ('AA', 'AA / AA+'),
                    ('A_AND_BELOW', 'A and Below'),
                    ('UNRATED', 'Unrated'),
                ],
                max_length=20,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name='securitymaster',
            name='ytm',
            field=models.DecimalField(
                blank=True,
                decimal_places=2,
                help_text='Yield to Maturity, percent.',
                max_digits=6,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name='securitymaster',
            name='modified_duration',
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=6, null=True),
        ),
        migrations.AddField(
            model_name='securitymaster',
            name='average_maturity',
            field=models.DecimalField(
                blank=True,
                decimal_places=2,
                help_text='Years.',
                max_digits=6,
                null=True,
            ),
        ),
    ]
