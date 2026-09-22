from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("portfolio_news", "0006_portfolionewsalert_interpretation_and_more"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="portfolionewsalert",
            index=models.Index(
                fields=[
                    "user",
                    "relevant",
                    "notification_tier",
                    "-created_at",
                ],
                name="news_alert_digest_filter_idx",
            ),
        ),
    ]
