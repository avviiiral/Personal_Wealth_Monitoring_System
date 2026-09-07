"""
Portfolio-news context for the existing PWMS AI chatbot
(ai/views.py:portfolio_chat).

This is NOT a second AI system: it only assembles structured
data from PortfolioNewsAlert for the same Gemini call the
portfolio chatbot already makes, the same way
ai/services/portfolio_context.py assembles holdings/transactions
data. The chatbot answers portfolio-news questions ("what
happened to my banking holdings this week?") by reading this
context, never by re-querying a news provider or re-running
analysis itself.

Kept deliberately compact (see MAX_ALERTS) since this data is
appended to every chat request's token budget - a few hundred
alerts' full summaries would meaningfully inflate cost on every
single chat message, most of which won't be about news at all.
"""

from datetime import timedelta

from django.utils import timezone


DEFAULT_LOOKBACK_DAYS = 30

MAX_ALERTS = 50


class PortfolioNewsChatContextBuilder:
    """
    Builds a user-scoped, size-bounded summary of recent
    portfolio news alerts for the AI chatbot's context.
    """

    @staticmethod
    def build(
        user,
        lookback_days: int = DEFAULT_LOOKBACK_DAYS,
        max_alerts: int = MAX_ALERTS,
    ) -> dict:

        from portfolio_news.models import PortfolioNewsAlert

        cutoff = timezone.now() - timedelta(days=lookback_days)

        queryset = (
            PortfolioNewsAlert.objects
            .filter(
                user=user,
                relevant=True,
                created_at__gte=cutoff,
            )
            .select_related("article")
            .order_by("-alert_score", "-created_at")
        )

        total_alerts = queryset.count()

        counts_by_tier: dict = {}

        for alert in queryset.values_list(
            "notification_tier", flat=True
        ):
            counts_by_tier[alert] = (
                counts_by_tier.get(alert, 0) + 1
            )

        alerts = []

        for alert in queryset[:max_alerts]:
            alerts.append({
                "holding": alert.holding_display_name,
                "holding_type": alert.holding_type,
                "category": alert.category,
                "sentiment": alert.sentiment,
                "impact": alert.impact,
                "materiality": getattr(
                    alert, "materiality", None
                ),
                "notification_tier": alert.notification_tier,
                "alert_score": alert.alert_score,
                "confidence": alert.confidence,
                "summary": alert.summary,
                "portfolio_implication": (
                    alert.portfolio_implication
                ),
                "key_facts": getattr(
                    alert, "key_facts", ""
                ),
                "uncertainty_notes": getattr(
                    alert, "uncertainty_notes", ""
                ),
                "article_source": alert.article.source,
                "article_published_at": (
                    alert.article.published_at.isoformat()
                    if alert.article.published_at
                    else None
                ),
                "created_at": alert.created_at.isoformat(),
                "is_read": alert.is_read,
            })

        return {
            "lookback_days": lookback_days,
            "total_alerts_in_window": total_alerts,
            "counts_by_notification_tier": counts_by_tier,
            "note": (
                "This is a summary of stored portfolio news "
                "alerts, capped at the most portfolio-relevant "
                f"{max_alerts}. It reflects only news the PWMS "
                "monitoring pipeline has already found and "
                "analyzed - it is not live/real-time news "
                "access."
            ),
            "alerts": alerts,
        }
