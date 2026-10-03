from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("portfolio_news", "0018_remove_portfolionewsmatch_unique_legacy_raw_news_match_per_holding_and_more"),
    ]

    operations = [
        migrations.AlterField(
            model_name="portfolionewsalert",
            name="source_type",
            field=models.CharField(
                choices=[
                    ("NEWS", "News"),
                    ("EXCHANGE_FILING", "Exchange Filing"),
                    ("CORPORATE_FILING", "Corporate Filing"),
                ],
                db_index=True,
                default="NEWS",
                max_length=30,
            ),
        ),
    ]
