import logging
from typing import TYPE_CHECKING, Tuple

from .alert_scoring import compute_alert_score, determine_notification_tier

if TYPE_CHECKING:
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

    The alert row records that an immediate notification is required
    through its notification_tier. Delivery is a separate concern:
    notification_sent stays False until the client/server notification
    delivery layer actually confirms delivery.
    """

    from ..models import PortfolioNewsAlert

    alert_score = compute_alert_score(
        impact_score=analysis.impact_score,
        portfolio_weight_percent=holding.portfolio_weight,
        confidence=analysis.confidence,
        source_quality=getattr(article, "source_quality", None),
        published_at=article.published_at,
    )

    notification_tier = determine_notification_tier(analysis.impact)

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
            "notification_sent": False,
            "materiality": analysis.materiality,
            "key_facts": analysis.key_facts,
            "interpretation": analysis.interpretation,
            "uncertainty_notes": analysis.uncertainty_notes,
        },
    )

    if created:
        logger.info(
            "Created alert id=%s user=%s holding=%r tier=%s score=%s",
            alert.pk,
            user.id,
            holding.display_name,
            notification_tier,
            alert_score,
        )

    return alert, created
