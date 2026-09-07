from datetime import timedelta

from django.shortcuts import get_object_or_404
from django.utils import timezone

from rest_framework.decorators import (
    api_view,
    permission_classes,
)
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .constants import NotificationTier
from .models import PortfolioNewsAlert
from .serializers import (
    PortfolioNewsAlertDetailSerializer,
    PortfolioNewsAlertListSerializer,
    PortfolioNewsDigestSerializer,
)
from .services.digest import build_daily_digest


DEFAULT_LIST_LIMIT = 50

MAX_LIST_LIMIT = 200

# ?date_range= values -> lookback window in days, applied
# against `created_at` (matches how the digest defines "today").
# "today" is handled separately below since it's a calendar-day
# bound, not a rolling window.
DATE_RANGE_DAYS = {
    "3d": 3,
    "7d": 7,
    "30d": 30,
}


def _parse_limit(request):

    try:
        limit = int(
            request.query_params.get(
                "limit",
                DEFAULT_LIST_LIMIT,
            )
        )
    except (TypeError, ValueError):
        limit = DEFAULT_LIST_LIMIT

    return max(1, min(limit, MAX_LIST_LIMIT))


def _apply_common_filters(queryset, request):
    """
    Shared filter logic for the news feed. Every filter is
    optional and silently ignored if the value isn't a
    recognized choice - an unknown/garbled filter value should
    narrow safely down to "no matches" via the ORM's normal
    behavior, not error the whole request.
    """

    category = request.query_params.get("category")

    if category:
        queryset = queryset.filter(category=category)

    sentiment = request.query_params.get("sentiment")

    if sentiment:
        queryset = queryset.filter(sentiment=sentiment)

    holding_type = request.query_params.get("holding_type")

    if holding_type:
        queryset = queryset.filter(holding_type=holding_type)

    holding_id = request.query_params.get("holding_id")

    if holding_id:
        try:
            queryset = queryset.filter(holding_id=int(holding_id))
        except (TypeError, ValueError):
            pass

    date_range = request.query_params.get("date_range")

    if date_range == "today":
        queryset = queryset.filter(
            created_at__date=timezone.localdate()
        )
    elif date_range in DATE_RANGE_DAYS:
        cutoff = timezone.now() - timedelta(
            days=DATE_RANGE_DAYS[date_range]
        )
        queryset = queryset.filter(created_at__gte=cutoff)

    return queryset


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def portfolio_news_list(request):
    """
    Browsable portfolio news feed for the authenticated user -
    every alert regardless of notification tier, newest/highest
    priority first. Supports optional filtering by notification
    tier, read state, category, sentiment, holding type/id, and
    a rolling or calendar-day date range.
    """

    queryset = (
        PortfolioNewsAlert.objects
        .filter(user=request.user, relevant=True)
        .select_related("article")
    )

    tier = request.query_params.get("tier")

    if tier:
        queryset = queryset.filter(notification_tier=tier)

    if request.query_params.get("unread_only") == "true":
        queryset = queryset.filter(is_read=False)

    queryset = _apply_common_filters(queryset, request)

    limit = _parse_limit(request)

    items = list(queryset[:limit])

    serializer = PortfolioNewsAlertListSerializer(
        items,
        many=True,
    )

    return Response(
        {
            "results": serializer.data,
            "count": len(serializer.data),
        }
    )


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def portfolio_news_detail(request, alert_id):
    """
    Full detail for one alert. Scoped to the authenticated
    user - an alert belonging to another user returns 404,
    never leaking whether it exists.
    """

    alert = get_object_or_404(
        PortfolioNewsAlert.objects.select_related("article"),
        id=alert_id,
        user=request.user,
        relevant=True,
    )

    serializer = PortfolioNewsAlertDetailSerializer(alert)

    return Response(serializer.data)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def portfolio_notifications_list(request):
    """
    The notification bell feed: unread CRITICAL/HIGH-tier
    alerts only, the ones the spec says should be notified
    immediately. MODERATE/LOW items are visible through
    /news/ but don't show up here.
    """

    base_queryset = (
        PortfolioNewsAlert.objects
        .filter(
            user=request.user,
            is_read=False,
            relevant=True,
            notification_tier__in=[
                NotificationTier.CRITICAL,
                NotificationTier.HIGH,
            ],
        )
    )

    unread_count = base_queryset.count()

    limit = _parse_limit(request)

    items = list(
        base_queryset
        .select_related("article")
        .order_by("-alert_score", "-created_at")[:limit]
    )

    serializer = PortfolioNewsAlertListSerializer(
        items,
        many=True,
    )

    return Response(
        {
            "unread_count": unread_count,
            "results": serializer.data,
        }
    )


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def mark_notification_read(request, alert_id):

    alert = get_object_or_404(
        PortfolioNewsAlert,
        id=alert_id,
        user=request.user,
    )

    if not alert.is_read:
        alert.is_read = True
        alert.save(update_fields=["is_read"])

    return Response(
        {
            "id": alert.id,
            "is_read": alert.is_read,
        }
    )


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def mark_all_notifications_read(request):

    updated = (
        PortfolioNewsAlert.objects
        .filter(
            user=request.user,
            is_read=False,
        )
        .update(is_read=True)
    )

    return Response(
        {
            "updated": updated,
        }
    )


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def portfolio_news_digest(request):
    """
    Daily portfolio news digest for the authenticated user -
    CRITICAL/HIGH/MODERATE alerts created on the given date
    (default: today), ordered by alert_score. See
    services/digest.py for exactly what's included and why.

    Accepts an optional ?date=YYYY-MM-DD query param; an
    unparseable or missing date falls back to today rather than
    erroring, since a slightly-wrong digest date is harmless and
    this endpoint is read-only.
    """

    from datetime import date as date_type

    for_date = None

    raw_date = request.query_params.get("date")

    if raw_date:
        try:
            for_date = date_type.fromisoformat(raw_date)
        except ValueError:
            for_date = None

    digest = build_daily_digest(request.user, for_date=for_date)

    serializer = PortfolioNewsDigestSerializer(digest)

    return Response(serializer.data)