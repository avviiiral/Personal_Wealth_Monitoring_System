"""Conservative deterministic unusual-news-activity detection."""

from dataclasses import dataclass
from datetime import timedelta

from django.db.models import Count, Q
from django.utils import timezone

from ..models import PortfolioNewsMatch
from config.pwms_config import get as get_pwms_config


@dataclass(frozen=True)
class UnusualNewsActivity:
    holding_type: str
    holding_id: int
    holding_display_name: str
    recent_count: int
    baseline_daily_average: float
    ratio: float


def detect_unusual_activity(
    user,
    holding,
    recent_hours: int | None = None,
    baseline_days: int | None = None,
) -> UnusualNewsActivity | None:
    config = get_pwms_config("news", "unusual_activity", {})
    recent_hours = config.get("recent_hours", 24) if recent_hours is None else recent_hours
    baseline_days = config.get("baseline_days", 30) if baseline_days is None else baseline_days
    minimum_recent_count = config.get("minimum_recent_count", 5)
    baseline_multiplier = config.get("baseline_multiplier", 2.5)

    now = timezone.now()
    recent_start = now - timedelta(hours=recent_hours)
    baseline_start = now - timedelta(days=baseline_days)

    base_qs = PortfolioNewsMatch.objects.filter(
        user=user,
        holding_type=holding.holding_type,
        holding_id=holding.holding_id,
        created_at__gte=baseline_start,
    )
    counts = base_qs.aggregate(
        total=Count("id"),
        recent=Count("id", filter=Q(created_at__gte=recent_start)),
    )
    recent_count = int(counts["recent"] or 0)
    baseline_count = max(0, int(counts["total"] or 0) - recent_count)
    baseline_days_effective = max(1, baseline_days - 1)
    baseline_average = baseline_count / baseline_days_effective

    threshold = max(minimum_recent_count, baseline_average * baseline_multiplier)
    ratio = recent_count / max(baseline_average, 1.0)

    if recent_count < threshold or recent_count < minimum_recent_count:
        return None

    return UnusualNewsActivity(
        holding_type=holding.holding_type,
        holding_id=holding.holding_id,
        holding_display_name=holding.display_name,
        recent_count=recent_count,
        baseline_daily_average=round(baseline_average, 2),
        ratio=round(ratio, 2),
    )
