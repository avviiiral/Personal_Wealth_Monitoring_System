from rest_framework import serializers

from .models import PortfolioNewsAlert


class PortfolioNewsAlertListSerializer(serializers.ModelSerializer):
    """
    Compact representation for the news feed and the
    notification bell dropdown.
    """

    article_title = serializers.CharField(
        source="article.title",
        read_only=True,
    )

    article_source = serializers.CharField(
        source="article.source",
        read_only=True,
    )

    article_published_at = serializers.DateTimeField(
        source="article.published_at",
        read_only=True,
    )

    source_quality = serializers.CharField(
        source="article.source_quality",
        read_only=True,
    )

    source_count = serializers.IntegerField(
        source="article.source_count",
        read_only=True,
    )

    class Meta:
        model = PortfolioNewsAlert

        fields = [
            "id",
            "holding_display_name",
            "holding_type",
            "category",
            "sentiment",
            "impact",
            "impact_score",
            "materiality",
            "alert_score",
            "notification_tier",
            "article_title",
            "article_source",
            "article_published_at",
            "source_quality",
            "source_count",
            "is_read",
            "notification_sent",
            "created_at",
        ]


class NewsArticleSourceSerializer(serializers.Serializer):
    """
    One publisher's report of the same underlying event. Plain
    Serializer (not ModelSerializer) since it's only ever used
    read-only, nested inside PortfolioNewsAlertDetailSerializer.
    """

    publisher_name = serializers.CharField()
    url = serializers.URLField()
    quality_tier = serializers.CharField()
    published_at = serializers.DateTimeField()


class PortfolioNewsDigestItemSerializer(serializers.Serializer):
    """
    Plain Serializer for one entry in a PortfolioNewsDigest
    dataclass (services/digest.py) - not a ModelSerializer since
    DigestItem is a dataclass, not a model instance.
    """

    alert_id = serializers.IntegerField()
    holding_display_name = serializers.CharField()
    holding_type = serializers.CharField()
    category = serializers.CharField()
    impact = serializers.CharField()
    materiality = serializers.CharField()
    sentiment = serializers.CharField()
    summary = serializers.CharField()
    alert_score = serializers.FloatField()
    source_count = serializers.IntegerField()


class PortfolioNewsDigestSerializer(serializers.Serializer):
    """
    Serializes a PortfolioNewsDigest dataclass (services/digest.py).
    """

    digest_date = serializers.DateField()
    item_count = serializers.IntegerField()
    items = PortfolioNewsDigestItemSerializer(many=True)


class PortfolioNewsAlertDetailSerializer(serializers.ModelSerializer):
    """
    Full representation for the news detail page - includes
    the AI's reasoning, the portfolio-weight context behind
    "why this matters to you", and a link to the original
    article (never the article body itself).
    """

    article_title = serializers.CharField(
        source="article.title",
        read_only=True,
    )

    article_source = serializers.CharField(
        source="article.source",
        read_only=True,
    )

    article_url = serializers.URLField(
        source="article.url",
        read_only=True,
    )

    article_published_at = serializers.DateTimeField(
        source="article.published_at",
        read_only=True,
    )

    article_description = serializers.CharField(
        source="article.description",
        read_only=True,
    )

    source_quality = serializers.CharField(
        source="article.source_quality",
        read_only=True,
    )

    source_count = serializers.IntegerField(
        source="article.source_count",
        read_only=True,
    )

    sources = NewsArticleSourceSerializer(
        source="article.sources",
        many=True,
        read_only=True,
    )

    class Meta:
        model = PortfolioNewsAlert

        fields = [
            "id",
            "holding_display_name",
            "holding_type",
            "category",
            "sentiment",
            "time_horizon",
            "relevance_score",
            "impact",
            "impact_score",
            "materiality",
            "confidence",
            "portfolio_weight_at_alert",
            "alert_score",
            "notification_tier",
            "summary",
            "portfolio_implication",
            "reason",
            "key_facts",
            "interpretation",
            "uncertainty_notes",
            "is_read",
            "notification_sent",
            "created_at",
            "article_title",
            "article_source",
            "article_url",
            "article_published_at",
            "article_description",
            "source_quality",
            "source_count",
            "sources",
        ]