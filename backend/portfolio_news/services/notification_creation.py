import logging

from typing import TYPE_CHECKING, Tuple

from users.permissions import get_active_family_group_id

from .alert_scoring import (
    compute_alert_score,
    determine_notification_tier,
    should_send_immediate_notification,
)

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
    Create/fetch an alert inside the user's active family scope.

    Production monitoring always runs with a valid active family. A
    legacy no-profile/no-active-family caller is retained temporarily
    for existing service tests and non-request background callers; its
    alert remains family_group=NULL and is not exposed by family-scoped
    APIs.
    """

    from ..models import PortfolioNewsAlert

    family_group_id = get_active_family_group_id(user)

    alert_score = compute_alert_score(
        impact_score=analysis.impact_score,
        portfolio_weight_percent=holding.portfolio_weight,
        confidence=analysis.confidence,
        source_quality=getattr(article, "source_quality", None),
        published_at=article.published_at,
    )

    notification_tier = determine_notification_tier(analysis.impact)
    notification_sent = should_send_immediate_notification(notification_tier)

    lookup = {
        "user": user,
        "article": article,
        "holding_type": holding.holding_type,
        "holding_id": holding.holding_id,
    }

    if family_group_id is not None:
        lookup["family_group_id"] = family_group_id

    defaults = {
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
    }

    if family_group_id is None:
        # Compatibility path for pre-family service tests/callers.
        alert = PortfolioNewsAlert.objects.filter(**lookup).first()
        if alert is not None:
            return alert, False

        alert = PortfolioNewsAlert.objects.create(
            family_group=None,
            **lookup,
            **defaults,
        )
        created = True
    else:
        alert, created = PortfolioNewsAlert.objects.get_or_create(
            **lookup,
            defaults=defaults,
        )

    if created:
        logger.info(
            "Created alert id=%s user=%s family_group=%s holding=%r tier=%s score=%s",
            alert.pk,
            user.id,
            family_group_id,
            holding.display_name,
            notification_tier,
            alert_score,
        )

    return alert, created
