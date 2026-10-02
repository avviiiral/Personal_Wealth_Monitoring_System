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

    filing_exchange = serializers.CharField(source="filing.exchange", read_only=True, allow_null=True)
    filing_company = serializers.CharField(source="filing.company_name", read_only=True, allow_null=True)
    filing_symbol = serializers.CharField(source="filing.symbol", read_only=True, allow_null=True)
    filing_subject = serializers.CharField(source="filing.subject", read_only=True, allow_null=True)
    filing_event_type = serializers.CharField(source="filing.event_type", read_only=True, allow_null=True)
    filing_severity = serializers.CharField(source="filing.severity", read_only=True, allow_null=True)

    class Meta:
        model = PortfolioNewsAlert

        fields = [
            "id",
            "source_type",
            "holding_display_name",
            "holding_type",
            "connection_type",
            "underlying_name",
            "underlying_weight",
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
            "filing_exchange",
            "filing_company",
            "filing_symbol",
            "filing_subject",
            "filing_event_type",
            "filing_severity",
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


class PortfolioNewsRawHoldingSerializer(serializers.Serializer):
    holding_type = serializers.CharField()
    holding_id = serializers.IntegerField()
    holding_display_name = serializers.CharField()
    connection_type = serializers.CharField()
    underlying_name = serializers.CharField(allow_blank=True)
    underlying_weight = serializers.DecimalField(
        max_digits=10,
        decimal_places=4,
        allow_null=True,
    )


class PortfolioNewsRawItemSerializer(serializers.Serializer):
    """
    Metadata-only representation of a deterministic portfolio-news match.
    No Gemini analysis is required for this feed.
    """

    id = serializers.IntegerField()
    title = serializers.CharField()
    url = serializers.URLField()
    source = serializers.CharField()
    description = serializers.CharField()
    published_at = serializers.DateTimeField(allow_null=True)
    source_quality = serializers.CharField()
    source_count = serializers.IntegerField()
    matched_query = serializers.CharField()
    created_at = serializers.DateTimeField()
    matched_holdings = PortfolioNewsRawHoldingSerializer(many=True)


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
    filing_exchange = serializers.CharField(source="filing.exchange", read_only=True, allow_null=True)
    filing_company = serializers.CharField(source="filing.company_name", read_only=True, allow_null=True)
    filing_symbol = serializers.CharField(source="filing.symbol", read_only=True, allow_null=True)
    filing_subject = serializers.CharField(source="filing.subject", read_only=True, allow_null=True)
    filing_event_type = serializers.CharField(source="filing.event_type", read_only=True, allow_null=True)
    filing_severity = serializers.CharField(source="filing.severity", read_only=True, allow_null=True)
    filing_url = serializers.URLField(source="filing.filing_url", read_only=True, allow_null=True)

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
            "source_type",
            "holding_display_name",
            "holding_type",
            "connection_type",
            "underlying_name",
            "underlying_weight",
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
            "filing_exchange",
            "filing_company",
            "filing_symbol",
            "filing_subject",
            "filing_event_type",
            "filing_severity",
            "filing_url",
        ]