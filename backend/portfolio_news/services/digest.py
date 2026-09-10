"""Daily portfolio news digest generation."""

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
    tz = timezone.get_current_timezone()
    start = timezone.make_aware(datetime.combine(for_date, time.min), tz)
    end = timezone.make_aware(datetime.combine(for_date, time.max), tz)
    return start, end


def build_daily_digest(
    user,
    family_group_id: Optional[int] = None,
    for_date: Optional[date_type] = None,
) -> PortfolioNewsDigest:
    """
    Build a daily digest for one user's alerts inside one family.

    family_group_id is mandatory for the family-scoped path. The
    optional default is retained only for compatibility with callers
    that may be updated in a later migration step; without a family
    the function returns an empty digest rather than falling back to
    user-only financial ownership.
    """
    from ..models import PortfolioNewsAlert

    resolved_date = for_date or timezone.localdate()
    start, end = _day_bounds(resolved_date)

    if family_group_id is None:
        return PortfolioNewsDigest(
            digest_date=resolved_date,
            item_count=0,
            items=[],
        )

    digest_tiers = (
        NotificationTier.CRITICAL,
        NotificationTier.HIGH,
        NotificationTier.MODERATE,
    )

    queryset = (
        PortfolioNewsAlert.objects
        .filter(
            user=user,
            family_group_id=family_group_id,
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
            materiality=getattr(alert, "materiality", ""),
            sentiment=alert.sentiment,
            summary=alert.summary,
            alert_score=alert.alert_score,
            source_count=getattr(alert.article, "source_count", 1),
        )
        for alert in queryset
    ]

    return PortfolioNewsDigest(
        digest_date=resolved_date,
        item_count=len(items),
        items=items,
    )
