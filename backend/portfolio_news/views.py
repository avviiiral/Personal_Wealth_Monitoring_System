from datetime import timedelta

from django.db.models import Exists, OuterRef, Q
from django.db.models.functions import TruncDate
from django.shortcuts import get_object_or_404
from django.utils import timezone

from rest_framework.decorators import (
    api_view,
    permission_classes,
)
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .constants import NotificationTier
from .models import NewsArticle, PortfolioNewsAlert, PortfolioNewsMatch, PushSubscription
from .serializers import (
    PortfolioNewsAlertDetailSerializer,
    PortfolioNewsAlertListSerializer,
    PortfolioNewsRawItemSerializer,
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


def _parse_after_id(request):
    """Return a positive incremental-feed cursor, or None."""

    try:
        after_id = int(request.query_params.get("after_id", ""))
    except (TypeError, ValueError):
        return None

    return after_id if after_id > 0 else None


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

    source_type = request.query_params.get("source_type")

    if source_type:
        if source_type == "CORPORATE_FILING":
            queryset = queryset.filter(
                Q(source_type="CORPORATE_FILING")
                | Q(source_type="EXCHANGE_FILING")
            )
        else:
            queryset = queryset.filter(source_type=source_type)

    holding_id = request.query_params.get("holding_id")

    if holding_id:
        try:
            queryset = queryset.filter(holding_id=int(holding_id))
        except (TypeError, ValueError):
            pass

    date_range = request.query_params.get("date_range")

    if date_range == "today":
        queryset = queryset.filter(
            created_at__gte=timezone.localdate(),
            created_at__lt=timezone.localdate() + timedelta(days=1),
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
        .select_related("article", "filing")
    )

    after_id = _parse_after_id(request)
    if after_id is not None:
        queryset = queryset.filter(id__gt=after_id)

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
def portfolio_news_raw_list(request):
    """
    Return deterministic portfolio-news matches without requiring Gemini.

    The article page is driven from NewsArticle rather than sorting a large
    PortfolioNewsMatch queryset and then applying DISTINCT. An EXISTS
    subquery lets the database stop at the first matching holding row for
    each article, while NewsArticle's publication index handles the feed
    ordering efficiently.
    """

    limit = _parse_limit(request)

    match_filter = {
        "user": request.user,
        "article_id": OuterRef("pk"),
    }

    holding_type = request.query_params.get("holding_type")
    if holding_type:
        match_filter["holding_type"] = holding_type

    holding_id = request.query_params.get("holding_id")
    if holding_id:
        try:
            match_filter["holding_id"] = int(holding_id)
        except (TypeError, ValueError):
            pass

    matching_articles = PortfolioNewsMatch.objects.filter(**match_filter)

    articles = (
        NewsArticle.objects
        .filter(Exists(matching_articles))
    )

    after_id = _parse_after_id(request)
    if after_id is not None:
        articles = articles.filter(id__gt=after_id)

    date_range = request.query_params.get("date_range")
    if date_range == "today":
        today_start = timezone.localdate()
        tomorrow_start = today_start + timedelta(days=1)
        articles = articles.filter(
            published_at__gte=timezone.make_aware(
                timezone.datetime.combine(today_start, timezone.datetime.min.time()),
                timezone.get_current_timezone(),
            ),
            published_at__lt=timezone.make_aware(
                timezone.datetime.combine(tomorrow_start, timezone.datetime.min.time()),
                timezone.get_current_timezone(),
            ),
        )
    elif date_range in DATE_RANGE_DAYS:
        cutoff = timezone.now() - timedelta(days=DATE_RANGE_DAYS[date_range])
        articles = articles.filter(published_at__gte=cutoff)

    article_ids = list(
        articles
        .order_by("-published_at", "-created_at", "-id")
        .values_list("id", flat=True)[:limit]
    )

    if not article_ids:
        return Response({"results": [], "count": 0})

    matches = list(
        PortfolioNewsMatch.objects
        .filter(
            user=request.user,
            article_id__in=article_ids,
        )
        .select_related("article")
        .order_by(
            "-article__published_at",
            "-article__created_at",
            "holding_display_name",
        )
    )

    grouped = {}
    for match in matches:
        article = match.article
        item = grouped.setdefault(
            article.id,
            {
                "id": article.id,
                "title": article.title,
                "url": article.url,
                "source": article.source,
                "description": article.description,
                "published_at": article.published_at,
                "source_quality": article.source_quality,
                "source_count": article.source_count,
                "matched_query": article.matched_query,
                "created_at": article.created_at,
                "matched_holdings": [],
            },
        )

        item["matched_holdings"].append(
            {
                "holding_type": match.holding_type,
                "holding_id": match.holding_id,
                "holding_display_name": match.holding_display_name,
            }
        )

    ordered_items = [
        grouped[article_id]
        for article_id in article_ids
        if article_id in grouped
    ]

    serializer = PortfolioNewsRawItemSerializer(
        ordered_items,
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
        PortfolioNewsAlert.objects
        .filter(
            id=alert_id,
            user=request.user,
            relevant=True,
        )
        .select_related("article", "filing")
        .prefetch_related(
            "article__sources",
        ),
        id=alert_id,
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
        .select_related("article", "filing")
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


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def push_config(request):
    """
    Return the public VAPID key needed by the browser to create a
    Web Push subscription. The private key is never exposed.
    """
    from django.conf import settings

    return Response(
        {
            "enabled": bool(getattr(settings, "WEB_PUSH_ENABLED", False)),
            "public_key": getattr(settings, "WEB_PUSH_VAPID_PUBLIC_KEY", ""),
        }
    )


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def push_subscribe(request):
    payload = request.data or {}
    endpoint = payload.get("endpoint")
    keys = payload.get("keys") or {}

    if not isinstance(endpoint, str) or not endpoint.strip():
        return Response(
            {"detail": "A push subscription endpoint is required."},
            status=400,
        )

    if not isinstance(keys, dict):
        return Response(
            {"detail": "Push subscription keys are required."},
            status=400,
        )

    p256dh = keys.get("p256dh")
    auth = keys.get("auth")

    if not isinstance(p256dh, str) or not p256dh.strip():
        return Response(
            {"detail": "The p256dh subscription key is required."},
            status=400,
        )

    if not isinstance(auth, str) or not auth.strip():
        return Response(
            {"detail": "The auth subscription key is required."},
            status=400,
        )

    subscription, created = PushSubscription.objects.update_or_create(
        endpoint=endpoint.strip(),
        defaults={
            "user": request.user,
            "p256dh": p256dh.strip(),
            "auth": auth.strip(),
            "user_agent": request.META.get("HTTP_USER_AGENT", "")[:1000],
            "enabled": True,
        },
    )

    return Response(
        {
            "id": subscription.id,
            "created": created,
            "enabled": subscription.enabled,
        },
        status=201 if created else 200,
    )


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def push_unsubscribe(request):
    endpoint = request.data.get("endpoint")

    if not isinstance(endpoint, str) or not endpoint.strip():
        return Response(
            {"detail": "A push subscription endpoint is required."},
            status=400,
        )

    updated = PushSubscription.objects.filter(
        user=request.user,
        endpoint=endpoint.strip(),
    ).update(enabled=False)

    return Response({"updated": updated})
