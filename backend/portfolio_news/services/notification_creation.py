import logging

from typing import TYPE_CHECKING, Tuple

from .alert_scoring import (
    compute_alert_score,
    determine_notification_tier,
    should_send_immediate_notification,
)

if TYPE_CHECKING:
    # Only imported for type-checking (Pylance/Pyright/mypy).
    # TYPE_CHECKING is always False at runtime, so this never
    # actually executes and cannot reintroduce the circular-import
    # problem the real, function-local import below was written to
    # avoid.
    from ..models import PortfolioNewsAlert


logger = logging.getLogger(__name__)


def create_alert_from_analysis(
    user,
    article,
    holding,
    analysis,
) -> Tuple["PortfolioNewsAlert", bool]:
    """
    Create (or fetch the existing) PortfolioNewsAlert for this
    exact (user, article, holding) combination.

    Idempotent by design: the model's unique constraint on
    (user, article, holding_type, holding_id) means calling
    this twice for the same combination returns the existing
    row on the second call instead of creating a duplicate -
    this is what makes `monitor_portfolio_news` safe to run
    repeatedly.
    """

    from ..models import PortfolioNewsAlert

    alert_score = compute_alert_score(
        impact_score=analysis.impact_score,
        portfolio_weight_percent=holding.portfolio_weight,
        confidence=analysis.confidence,
        source_quality=getattr(article, "source_quality", None),
        published_at=article.published_at,
    )

    notification_tier = determine_notification_tier(
        analysis.impact
    )

    notification_sent = should_send_immediate_notification(
        notification_tier
    )

    alert, created = PortfolioNewsAlert.objects.get_or_create(
        user=user,
        article=article,
        holding_type=holding.holding_type,
        holding_id=holding.holding_id,
        defaults={
            "holding_display_name": holding.display_name,
            "relevant": analysis.relevant,
            "category": analysis.category,
            "sentiment": analysis.sentiment,
            "time_horizon": analysis.time_horizon,
            "relevance_score": analysis.relevance_score,
            "impact": analysis.impact,
            "impact_score": analysis.impact_score,
            "confidence": analysis.confidence,
            "portfolio_weight_at_alert": holding.portfolio_weight,
            "alert_score": alert_score,
            "notification_tier": notification_tier,
            "summary": analysis.summary,
            "portfolio_implication": analysis.portfolio_implication,
            "reason": analysis.reason,
            "notification_sent": notification_sent,
            "materiality": analysis.materiality,
            "key_facts": analysis.key_facts,
            "interpretation": analysis.interpretation,
            "uncertainty_notes": analysis.uncertainty_notes,
        },
    )

    if created:
        logger.info(
            "Created alert id=%s user=%s holding=%r tier=%s "
            "score=%s",
            alert.pk,
            user.id,
            holding.display_name,
            notification_tier,
            alert_score,
        )

    return alert, created