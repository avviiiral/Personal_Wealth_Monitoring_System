from datetime import datetime
from typing import Optional

from django.utils import timezone

from ..constants import NotificationTier, SourceQualityTier


def _recency_weight(
    published_at: Optional[datetime],
    now: Optional[datetime] = None,
) -> float:
    """
    Recency multiplier (0.5-1.0) applied to alert_score.

    Deterministic, documented decay:
        <= 1 day old:  1.0  (no decay - "fresh" news)
        1-7 days old:  linearly decays from 1.0 to 0.5
        > 7 days old:  0.5  (floor - still relevant, just not
                             breaking news)

    published_at=None (article has no known publish date) is
    treated as neutral (1.0) rather than penalized, since a
    missing date is a data-quality gap, not evidence the story
    is stale.
    """

    if published_at is None:
        return 1.0

    now = now or timezone.now()

    age = now - published_at
    age_days = age.total_seconds() / 86400.0

    if age_days <= 1:
        return 1.0

    if age_days >= 7:
        return 0.5

    # Linear interpolation between (1 day, 1.0) and (7 days, 0.5).
    fraction = (age_days - 1) / (7 - 1)

    return round(1.0 - fraction * 0.5, 4)


def compute_alert_score(
    impact_score: int,
    portfolio_weight_percent: float,
    confidence: float,
    source_quality: Optional[str] = None,
    published_at: Optional[datetime] = None,
    now: Optional[datetime] = None,
) -> float:
    """
    Internal alert-priority score, NOT a prediction of future
    returns or a financial risk model.

    Base formula (unchanged from the original spec, and the only
    factors used when source_quality/published_at are omitted -
    this keeps every existing caller's score identical):

        score = impact_score x (portfolio_weight_percent / 100) x confidence

    When source_quality and/or published_at are supplied, two
    additional multipliers are folded in:

        source_quality_weight: SourceQualityTier.weight(tier),
            0.5-1.0. A critical-sounding claim from an
            unclassified blog should rank below the same claim
            corroborated by Reuters/exchange filings.

        recency_weight: see _recency_weight(). 0.5-1.0. Stale
            news matters less than the same story breaking today.

    Both default to a neutral 1.0 multiplier when not supplied,
    so this is purely additive - it never changes a score computed
    the old way. Still ranges 0-100.

    Example from the spec (base formula only):
        Company A: impact=90, weight=2%  -> 90 * 0.02 = 1.8
        Company B: impact=80, weight=25% -> 80 * 0.25 = 20.0
        (Company B ranks higher, as intended.)
    """

    weight_fraction = max(0.0, portfolio_weight_percent) / 100.0

    source_quality_weight = (
        SourceQualityTier.weight(source_quality)
        if source_quality is not None
        else 1.0
    )

    recency_weight = _recency_weight(published_at, now=now)

    score = (
        impact_score
        * weight_fraction
        * confidence
        * source_quality_weight
        * recency_weight
    )

    return round(max(0.0, min(100.0, score)), 2)


def determine_notification_tier(impact_level: str) -> str:
    return NotificationTier.from_impact_level(impact_level)


def should_send_immediate_notification(
    notification_tier: str,
) -> bool:
    return notification_tier in (
        NotificationTier.CRITICAL,
        NotificationTier.HIGH,
    )


def should_include_in_digest(notification_tier: str) -> bool:
    return notification_tier == NotificationTier.MODERATE