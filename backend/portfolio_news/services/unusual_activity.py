"""Conservative deterministic unusual-news-activity detection."""

from dataclasses import dataclass
from datetime import timedelta

from django.db.models import Count, Q
from django.utils import timezone

from ..models import PortfolioNewsMatch


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
    recent_hours: int = 24,
    baseline_days: int = 30,
) -> UnusualNewsActivity | None:
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

    threshold = max(5, baseline_average * 2.5)
    ratio = recent_count / max(baseline_average, 1.0)

    if recent_count < threshold or recent_count < 5:
        return None

    return UnusualNewsActivity(
        holding_type=holding.holding_type,
        holding_id=holding.holding_id,
        holding_display_name=holding.display_name,
        recent_count=recent_count,
        baseline_daily_average=round(baseline_average, 2),
        ratio=round(ratio, 2),
    )
