from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("portfolio_news", "0008_rename_portfolionewsalert_indexes"),
    ]

    operations = [
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
                help_text="Best (highest) SourceQualityTier among all NewsArticleSource rows for this event.",
                max_length=20,
            ),
        ),
        migrations.AlterField(
            model_name="newsarticle",
            name="source_count",
            field=models.PositiveSmallIntegerField(
                default=1,
                help_text="Number of distinct publishers that have reported this event.",
            ),
        ),
        migrations.AlterField(
            model_name="portfolionewsalert",
            name="holding_id",
            field=models.PositiveIntegerField(
                help_text=(
                    "Primary key of the Asset (equity) or MutualFundScheme this "
                    "alert is about. Not a database FK because it can point to "
                    "either model."
                ),
            ),
        ),
        migrations.AlterField(
            model_name="portfolionewsalert",
            name="holding_display_name",
            field=models.CharField(max_length=300),
        ),
        migrations.AlterField(
            model_name="portfolionewsalert",
            name="relevant",
            field=models.BooleanField(default=True),
        ),
        migrations.AlterField(
            model_name="portfolionewsalert",
            name="portfolio_weight_at_alert",
            field=models.FloatField(
                help_text="Snapshot of the holding's portfolio weight percentage at alert creation time."
            ),
        ),
        migrations.AlterField(
            model_name="portfolionewsalert",
            name="alert_score",
            field=models.FloatField(
                help_text=(
                    "Internal alert-priority score (impact_score x portfolio weight "
                    "x confidence, 0-100). NOT a prediction of future returns."
                )
            ),
        ),
        migrations.AlterField(
            model_name="portfolionewsalert",
            name="materiality",
            field=models.CharField(
                choices=[
                    ("trivial", "Trivial"),
                    ("low", "Low"),
                    ("moderate", "Moderate"),
                    ("high", "High"),
                    ("critical", "Critical"),
                ],
                default="moderate",
                max_length=20,
            ),
        ),
        migrations.AlterField(
            model_name="portfolionewsalert",
            name="key_facts",
            field=models.TextField(blank=True),
        ),
        migrations.AlterField(
            model_name="portfolionewsalert",
            name="interpretation",
            field=models.TextField(blank=True),
        ),
        migrations.AlterField(
            model_name="portfolionewsalert",
            name="uncertainty_notes",
            field=models.TextField(blank=True),
        ),
    ]
