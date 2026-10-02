from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("portfolio_news", "0013_portfolionews_underlying_connection"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="portfolionewsmatch",
            name="unique_legacy_raw_news_match_per_holding",
        ),
        migrations.RemoveConstraint(
            model_name="portfolionewsmatch",
            name="unique_family_raw_news_match_per_holding",
        ),
        migrations.AddConstraint(
            model_name="portfolionewsmatch",
            constraint=models.UniqueConstraint(
                condition=models.Q(family__isnull=True),
                fields=(
                    "user",
                    "article",
                    "holding_type",
                    "holding_id",
                    "connection_type",
                    "underlying_name",
                ),
                name="unique_legacy_raw_news_match_connection",
            ),
        ),
        migrations.AddConstraint(
            model_name="portfolionewsmatch",
            constraint=models.UniqueConstraint(
                condition=models.Q(family__isnull=False),
                fields=(
                    "family",
                    "article",
                    "holding_type",
                    "holding_id",
                    "connection_type",
                    "underlying_name",
                ),
                name="unique_family_raw_news_match_connection",
            ),
        ),
    ]
