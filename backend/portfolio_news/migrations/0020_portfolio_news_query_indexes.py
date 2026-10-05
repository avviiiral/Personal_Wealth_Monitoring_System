from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("portfolio_news", "0019_corporate_filing_source_type"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="newsarticle",
            index=models.Index(
                fields=["fingerprint", "-published_at"],
                name="news_article_fp_pub_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="portfolionewsmatch",
            index=models.Index(
                fields=["user", "article"],
                name="news_match_user_article_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="portfolionewsalert",
            index=models.Index(
                fields=["user", "relevant", "-created_at"],
                name="news_alert_user_rel_created_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="portfolionewsalert",
            index=models.Index(
                fields=["user", "notification_tier", "-created_at"],
                name="news_alert_user_tier_created_idx",
            ),
        ),
    ]
