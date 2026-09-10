from django.conf import settings
from django.db import models

from .constants import (
    HoldingType,
    ImpactLevel,
    Materiality,
    NewsCategory,
    NotificationTier,
    Sentiment,
    SourceQualityTier,
    TimeHorizon,
)


class NewsArticle(models.Model):
    """A single deduplicated, globally shared news article."""

    title = models.CharField(max_length=500)
    normalized_title = models.CharField(max_length=500, db_index=True)
    url = models.URLField(max_length=1000)
    url_hash = models.CharField(max_length=64, unique=True, db_index=True)
    source = models.CharField(max_length=200, blank=True)
    description = models.TextField(blank=True)
    published_at = models.DateTimeField(null=True, blank=True)
    fingerprint = models.CharField(
        max_length=64,
        db_index=True,
        help_text=(
            "Hash of normalized_title + published date bucket. "
            "Used to detect the same event reported by multiple "
            "sources on the same day."
        ),
    )
    matched_query = models.CharField(
        max_length=255,
        blank=True,
        help_text="The search query that first surfaced this article.",
    )
    source_quality = models.CharField(
        max_length=20,
        choices=SourceQualityTier.choices,
        default=SourceQualityTier.TIER_3,
        help_text=(
            "Best (highest) SourceQualityTier among all NewsArticleSource "
            "rows for this event."
        ),
    )
    source_count = models.PositiveSmallIntegerField(
        default=1,
        help_text="Number of distinct publishers that have reported this event.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-published_at", "-created_at"]
        indexes = [
            models.Index(fields=["fingerprint"], name="news_article_fingerprint_idx"),
            models.Index(fields=["normalized_title"], name="news_article_norm_title_idx"),
            models.Index(fields=["-published_at"], name="news_article_published_idx"),
        ]

    def __str__(self):
        return f"{self.title} ({self.source})"


class NewsArticleSource(models.Model):
    """One publisher's report of a globally shared NewsArticle."""

    article = models.ForeignKey(
        NewsArticle,
        on_delete=models.CASCADE,
        related_name="sources",
    )
    publisher_name = models.CharField(max_length=200, blank=True)
    url = models.URLField(max_length=1000)
    url_hash = models.CharField(max_length=64, db_index=True)
    quality_tier = models.CharField(
        max_length=20,
        choices=SourceQualityTier.choices,
        default=SourceQualityTier.TIER_3,
    )
    published_at = models.DateTimeField(null=True, blank=True)
    first_seen_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["quality_tier", "-first_seen_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["article", "url_hash"],
                name="unique_source_per_article_url",
            ),
        ]
        indexes = [
            models.Index(
                fields=["article", "quality_tier"],
                name="news_source_article_tier_idx",
            ),
        ]

    def __str__(self):
        return f"{self.publisher_name} -> article_id={self.article_id}"


class PortfolioNewsAlert(models.Model):
    """
    A news article's impact on one user's holding.

    `family_group` is the financial-data ownership boundary.
    `user` remains the notification/recipient identity and audit
    trail. Both are retained so multiple family members can receive
    alerts for the same family portfolio without mixing families.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="portfolio_news_alerts",
    )

    family_group = models.ForeignKey(
        "users.FamilyGroup",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="portfolio_news_alerts",
    )

    article = models.ForeignKey(
        NewsArticle,
        on_delete=models.CASCADE,
        related_name="alerts",
    )

    holding_type = models.CharField(
        max_length=20,
        choices=HoldingType.choices,
    )

    holding_id = models.PositiveIntegerField(
        help_text=(
            "Primary key of the Asset (equity) or MutualFundScheme this "
            "alert is about. Not a database FK because it can point to "
            "either model."
        ),
    )

    holding_display_name = models.CharField(max_length=300)

    relevant = models.BooleanField(default=True)
    category = models.CharField(max_length=30, choices=NewsCategory.choices)
    sentiment = models.CharField(max_length=20, choices=Sentiment.choices)
    time_horizon = models.CharField(max_length=20, choices=TimeHorizon.choices)
    relevance_score = models.PositiveSmallIntegerField()
    impact = models.CharField(max_length=20, choices=ImpactLevel.choices)
    impact_score = models.PositiveSmallIntegerField()
    confidence = models.FloatField()

    portfolio_weight_at_alert = models.FloatField(
        help_text="Snapshot of the holding's portfolio weight percentage at alert creation time."
    )

    alert_score = models.FloatField(
        help_text=(
            "Internal alert-priority score (impact_score x portfolio weight "
            "x confidence, 0-100). NOT a prediction of future returns."
        )
    )

    notification_tier = models.CharField(
        max_length=20,
        choices=NotificationTier.choices,
    )

    summary = models.TextField()
    portfolio_implication = models.TextField()
    reason = models.TextField()

    materiality = models.CharField(
        max_length=20,
        choices=Materiality.choices,
        default=Materiality.MODERATE,
    )

    key_facts = models.TextField(blank=True)
    interpretation = models.TextField(blank=True)
    uncertainty_notes = models.TextField(blank=True)
    is_read = models.BooleanField(default=False)
    notification_sent = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-alert_score", "-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=[
                    "user",
                    "family_group",
                    "article",
                    "holding_type",
                    "holding_id",
                ],
                name="unique_alert_per_user_family_article_holding",
            ),
        ]
        indexes = [
            models.Index(
                fields=["user", "family_group", "-created_at"],
                name="news_alert_user_family_created_idx",
            ),
            models.Index(
                fields=["user", "family_group", "is_read"],
                name="news_alert_user_family_unread_idx",
            ),
            models.Index(
                fields=["user", "family_group", "notification_tier"],
                name="news_alert_user_family_tier_idx",
            ),
            models.Index(
                fields=["family_group", "-created_at"],
                name="news_alert_family_created_idx",
            ),
        ]

    def __str__(self):
        return (
            f"{self.holding_display_name} - "
            f"{self.article.title} ({self.notification_tier})"
        )
