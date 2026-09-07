"""
Digest generation.

Per the spec: "If five articles describe the same event, do NOT
generate five notifications." That part is already handled
upstream - ArticleDeduplicator/store_article collapse same-event
articles into one NewsArticle (see source_quality.py,
article_store.py), so there is only ever one PortfolioNewsAlert
per (user, article, holding) regardless of how many publishers
covered the story. NewsArticle.source_count is how the UI shows
"Reported by N sources".

This module covers the other half: MODERATE-tier alerts are
intentionally not sent as immediate notifications
(should_include_in_digest in alert_scoring.py), so they need
somewhere to go. build_daily_digest() groups a user's qualifying
alerts from a given day into a single ordered summary suitable
for a "Morning Portfolio News Digest" - either rendered by an API
endpoint or handed to a future email/push channel.
"""

from dataclasses import dataclass, field
from datetime import date as date_type, datetime, time
from typing import List, Optional

from django.utils import timezone

from ..constants import NotificationTier


@dataclass
class DigestItem:
    alert_id: int
    holding_display_name: str
    holding_type: str
    category: str
    impact: str
    materiality: str
    sentiment: str
    summary: str
    alert_score: float
    source_count: int


@dataclass
class PortfolioNewsDigest:
    digest_date: date_type
    item_count: int
    items: List[DigestItem] = field(default_factory=list)


def _day_bounds(for_date: date_type):
    """
    Returns (start, end) timezone-aware datetimes spanning the
    given calendar date in the current timezone, matching how
    "27 Aug 2026" is understood by a human reading the digest
    rather than by raw UTC offsets.
    """

    tz = timezone.get_current_timezone()

    start = timezone.make_aware(
        datetime.combine(for_date, time.min), tz
    )

    end = timezone.make_aware(
        datetime.combine(for_date, time.max), tz
    )

    return start, end


def build_daily_digest(
    user,
    for_date: Optional[date_type] = None,
) -> PortfolioNewsDigest:
    """
    Builds a daily recap of a user's notification-worthy
    portfolio news alerts created on `for_date` (default: today,
    in the current timezone).

    Includes CRITICAL, HIGH, and MODERATE tier alerts - i.e.
    everything the spec's example digest shows ("High Impact",
    "Medium Impact" items side by side). CRITICAL/HIGH alerts
    already went out as immediate notifications
    (should_send_immediate_notification); appearing here too is
    intentional - the digest is a recap, not a second delivery
    channel, so it is safe to include an already-notified alert.
    LOW-tier alerts are reference-only and are never surfaced in
    the digest, matching should_include_in_digest's LOW
    exclusion.

    Ordered by alert_score descending, so the most portfolio-
    relevant item leads - matching the spec's example digest
    ("1. Company A - High Impact ... 2. Company B ...").
    """

    from ..models import PortfolioNewsAlert

    resolved_date = for_date or timezone.localdate()

    start, end = _day_bounds(resolved_date)

    digest_tiers = (
        NotificationTier.CRITICAL,
        NotificationTier.HIGH,
        NotificationTier.MODERATE,
    )

    queryset = (
        PortfolioNewsAlert.objects
        .filter(
            user=user,
            relevant=True,
            notification_tier__in=digest_tiers,
            created_at__gte=start,
            created_at__lte=end,
        )
        .select_related("article")
        .order_by("-alert_score", "-created_at")
    )

    items = [
        DigestItem(
            alert_id=alert.id,
            holding_display_name=alert.holding_display_name,
            holding_type=alert.holding_type,
            category=alert.category,
            impact=alert.impact,
            materiality=getattr(
                alert, "materiality", ""
            ),
            sentiment=alert.sentiment,
            summary=alert.summary,
            alert_score=alert.alert_score,
            source_count=getattr(
                alert.article, "source_count", 1
            ),
        )
        for alert in queryset
    ]

    return PortfolioNewsDigest(
        digest_date=resolved_date,
        item_count=len(items),
        items=items,
    )
