from datetime import timedelta

from django.shortcuts import get_object_or_404
from django.utils import timezone

from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from users.permissions import get_active_family_group_id

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
DATE_RANGE_DAYS = {"3d": 3, "7d": 7, "30d": 30}


def _active_family_id_or_none(request):
    return get_active_family_group_id(request.user)


def _parse_limit(request):
    try:
        limit = int(request.query_params.get("limit", DEFAULT_LIST_LIMIT))
    except (TypeError, ValueError):
        limit = DEFAULT_LIST_LIMIT
    return max(1, min(limit, MAX_LIST_LIMIT))


def _apply_common_filters(queryset, request):
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
        queryset = queryset.filter(created_at__date=timezone.localdate())
    elif date_range in DATE_RANGE_DAYS:
        cutoff = timezone.now() - timedelta(days=DATE_RANGE_DAYS[date_range])
        queryset = queryset.filter(created_at__gte=cutoff)

    return queryset


def _family_scoped_alerts(request):
    family_id = _active_family_id_or_none(request)
    if family_id is None:
        return None

    return PortfolioNewsAlert.objects.filter(
        user=request.user,
        family_group_id=family_id,
    )


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def portfolio_news_list(request):
    queryset = _family_scoped_alerts(request)
    if queryset is None:
        return Response({"results": [], "count": 0})

    queryset = (
        queryset
        .filter(relevant=True)
        .select_related("article")
    )

    tier = request.query_params.get("tier")
    if tier:
        queryset = queryset.filter(notification_tier=tier)

    if request.query_params.get("unread_only") == "true":
        queryset = queryset.filter(is_read=False)

    queryset = _apply_common_filters(queryset, request)
    items = list(queryset[:_parse_limit(request)])
    serializer = PortfolioNewsAlertListSerializer(items, many=True)

    return Response({"results": serializer.data, "count": len(serializer.data)})


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def portfolio_news_detail(request, alert_id):
    queryset = _family_scoped_alerts(request)
    if queryset is None:
        return Response({"detail": "No active family selected."}, status=404)

    alert = get_object_or_404(
        queryset.select_related("article"),
        id=alert_id,
        relevant=True,
    )
    return Response(PortfolioNewsAlertDetailSerializer(alert).data)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def portfolio_notifications_list(request):
    queryset = _family_scoped_alerts(request)
    if queryset is None:
        return Response({"unread_count": 0, "results": []})

    base_queryset = queryset.filter(
        is_read=False,
        relevant=True,
        notification_tier__in=[
            NotificationTier.CRITICAL,
            NotificationTier.HIGH,
        ],
    )

    unread_count = base_queryset.count()
    items = list(
        base_queryset
        .select_related("article")
        .order_by("-alert_score", "-created_at")[:_parse_limit(request)]
    )
    serializer = PortfolioNewsAlertListSerializer(items, many=True)

    return Response({"unread_count": unread_count, "results": serializer.data})


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def mark_notification_read(request, alert_id):
    queryset = _family_scoped_alerts(request)
    if queryset is None:
        return Response({"detail": "No active family selected."}, status=404)

    alert = get_object_or_404(queryset, id=alert_id)
    if not alert.is_read:
        alert.is_read = True
        alert.save(update_fields=["is_read"])

    return Response({"id": alert.id, "is_read": alert.is_read})


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def mark_all_notifications_read(request):
    queryset = _family_scoped_alerts(request)
    if queryset is None:
        return Response({"updated": 0})

    updated = queryset.filter(is_read=False).update(is_read=True)
    return Response({"updated": updated})


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def portfolio_news_digest(request):
    family_id = _active_family_id_or_none(request)
    if family_id is None:
        return Response({"detail": "No active family selected."}, status=404)

    from datetime import date as date_type

    for_date = None
    raw_date = request.query_params.get("date")
    if raw_date:
        try:
            for_date = date_type.fromisoformat(raw_date)
        except ValueError:
            for_date = None

    digest = build_daily_digest(
        request.user,
        family_group_id=family_id,
        for_date=for_date,
    )
    return Response(PortfolioNewsDigestSerializer(digest).data)
