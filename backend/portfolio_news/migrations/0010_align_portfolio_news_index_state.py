from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("portfolio_news", "0009_align_portfolio_news_field_metadata"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.RemoveIndex(
                    model_name="portfolionewsalert",
                    name="news_alert_user_created_idx",
                ),
                migrations.RemoveIndex(
                    model_name="portfolionewsalert",
                    name="news_alert_user_unread_idx",
                ),
                migrations.RemoveIndex(
                    model_name="portfolionewsalert",
                    name="news_alert_user_tier_idx",
                ),
            ],
        ),
        migrations.AlterField(
            model_name="newsarticle",
            name="source_quality",
            field=models.CharField(
                choices=[
                    ("tier_1", "Primary / Top-Tier"),
                    ("tier_2", "Reputable"),
                    ("tier_3", "Unclassified"),
                ],
                default="tier_3",
                help_text="Best source-quality tier among all sources for this event.",
                max_length=20,
            ),
        ),
    ]
