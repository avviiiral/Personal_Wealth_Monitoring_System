from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("watchlist", "0007_fund_manager_person_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="mutualfundproduct",
            name="fund_manager_checked_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="pmsproduct",
            name="fund_manager_checked_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
