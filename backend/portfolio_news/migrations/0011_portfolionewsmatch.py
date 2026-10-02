from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def backfill_portfolio_news_matches(apps, schema_editor):
    PortfolioNewsAlert = apps.get_model("portfolio_news", "PortfolioNewsAlert")
    PortfolioNewsMatch = apps.get_model("portfolio_news", "PortfolioNewsMatch")

    rows = []
    for alert in PortfolioNewsAlert.objects.all().iterator():
        rows.append(
            PortfolioNewsMatch(
                user_id=alert.user_id,
                article_id=alert.article_id,
                holding_type=alert.holding_type,
                holding_id=alert.holding_id,
                holding_display_name=alert.holding_display_name,
            )
        )

    if rows:
        PortfolioNewsMatch.objects.bulk_create(
            rows,
            ignore_conflicts=True,
            batch_size=500,
        )


class Migration(migrations.Migration):

    dependencies = [
        ("portfolio_news", "0010_pushsubscription"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="PortfolioNewsMatch",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "holding_type",
                    models.CharField(
                        choices=[
                            ("EQUITY", "Equity"),
                            ("MUTUAL_FUND", "Mutual Fund"),
                            ("WATCHLIST", "Watchlist"),
                        ],
                        max_length=20,
                    ),
                ),
                (
                    "holding_id",
                    models.PositiveIntegerField(
                        help_text=(
                            "Primary key of the Asset or MutualFundScheme "
                            "matched by the deterministic portfolio-news matcher."
                        )
                    ),
                ),
                ("holding_display_name", models.CharField(max_length=300)),
                ("matched_query", models.CharField(blank=True, max_length=255)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "article",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="portfolio_matches",
                        to="portfolio_news.newsarticle",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="portfolio_news_matches",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ["-created_at"],
                "indexes": [
                    models.Index(
                        fields=["user", "-created_at"],
                        name="news_match_user_created_idx",
                    ),
                    models.Index(
                        fields=["user", "holding_type", "holding_id"],
                        name="news_match_user_holding_idx",
                    ),
                ],
                "constraints": [
                    models.UniqueConstraint(
                        fields=["user", "article", "holding_type", "holding_id"],
                        name="unique_raw_news_match_per_holding",
                    )
                ],
            },
        ),
        migrations.RunPython(
            backfill_portfolio_news_matches,
            migrations.RunPython.noop,
        ),
    ]
