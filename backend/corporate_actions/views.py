from django.core.exceptions import ValidationError
from django.db import IntegrityError
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from users.permissions import CanWriteFinancialData
from .models import CorporateAction, CorporateActionStatus
from .services import resolve_corporate_action_candidates


def _serialize(action):
    return {
        "id": action.id, "action_type": action.action_type,
        "security_name": action.security_name, "isin": action.isin,
        "symbol": action.symbol, "exchange": action.exchange,
        "ex_date": action.ex_date, "record_date": action.record_date,
        "payment_date": action.payment_date, "old_units": action.old_units,
        "new_units": action.new_units, "cash_per_unit": action.cash_per_unit,
        "resulting_isin": action.resulting_isin, "resulting_symbol": action.resulting_symbol,
        "source": action.source, "source_reference": action.source_reference,
        "status": action.status, "notes": action.notes, "metadata": action.metadata,
        "created_at": action.created_at, "updated_at": action.updated_at,
        "quantity_factor": action.quantity_factor,
    }


@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated, CanWriteFinancialData])
def corporate_action_list(request):
    if request.method == "GET":
        queryset = CorporateAction.objects.filter(owner=request.user)
        status_filter = request.query_params.get("status")
        if status_filter in CorporateActionStatus.values:
            queryset = queryset.filter(status=status_filter)
        isin = request.query_params.get("isin", "").strip()
        symbol = request.query_params.get("symbol", "").strip()
        if isin:
            queryset = queryset.filter(isin__iexact=isin)
        elif symbol:
            queryset = queryset.filter(symbol__iexact=symbol)
        return Response({"count": queryset.count(), "results": [_serialize(a) for a in queryset]})

    allowed = {
        "action_type", "security_name", "isin", "symbol", "exchange", "ex_date",
        "record_date", "payment_date", "old_units", "new_units", "cash_per_unit",
        "resulting_isin", "resulting_symbol", "source", "source_reference",
        "status", "notes", "metadata",
    }
    payload = {key: value for key, value in request.data.items() if key in allowed}
    action = CorporateAction(owner=request.user, **payload)
    try:
        action.full_clean()
        action.save()
    except ValidationError as exc:
        return Response({"detail": exc.message_dict if hasattr(exc, "message_dict") else exc.messages}, status=400)
    except IntegrityError:
        return Response({"detail": "An action with this security, type and ex-date already exists."}, status=409)
    return Response(_serialize(action), status=status.HTTP_201_CREATED)


@api_view(["GET", "PATCH", "DELETE"])
@permission_classes([IsAuthenticated, CanWriteFinancialData])
def corporate_action_detail(request, action_id):
    action = CorporateAction.objects.filter(owner=request.user, id=action_id).first()
    if action is None:
        return Response({"detail": "Corporate action not found."}, status=404)
    if request.method == "GET":
        return Response(_serialize(action))
    if request.method == "DELETE":
        action.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    allowed = {
        "action_type", "security_name", "isin", "symbol", "exchange", "ex_date",
        "record_date", "payment_date", "old_units", "new_units", "cash_per_unit",
        "resulting_isin", "resulting_symbol", "source", "source_reference",
        "status", "notes", "metadata",
    }
    for key, value in request.data.items():
        if key in allowed:
            setattr(action, key, value)
    try:
        action.full_clean()
        action.save()
    except ValidationError as exc:
        return Response({"detail": exc.message_dict if hasattr(exc, "message_dict") else exc.messages}, status=400)
    except IntegrityError:
        return Response({"detail": "An action with this security, type and ex-date already exists."}, status=409)
    return Response(_serialize(action))


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def corporate_action_candidates(request):
    event_date = request.query_params.get("event_date")
    try:
        candidates = resolve_corporate_action_candidates(
            owner=request.user,
            isin=request.query_params.get("isin", ""),
            symbol=request.query_params.get("symbol", ""),
            exchange=request.query_params.get("exchange", ""),
            event_date=event_date,
        )
    except (TypeError, ValueError, ValidationError):
        return Response({"detail": "event_date must be an ISO date (YYYY-MM-DD)."}, status=400)
    return Response({
        "count": candidates.count(),
        "results": [_serialize(action) for action in candidates],
        "matching_policy": "Exact ISIN when supplied; otherwise exact symbol and optional exchange. No fuzzy auto-merging.",
    })


from django.db.models import Q
from django.utils import timezone
from .models import (
    CorporateActionExposure,
    CorporateActionNotification,
    CorporateActionReconciliation,
    CorporateActionReconciliationStatus,
)
from .services import update_notification_state


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def corporate_action_notifications(request):
    queryset = CorporateActionNotification.objects.filter(owner=request.user).select_related(
        "action", "reconciliation"
    ).prefetch_related("action__exposures")
    status_filter = request.query_params.get("status", "").upper()
    if status_filter == "NEW":
        queryset = queryset.filter(reviewed=False, dismissed=False)
    elif status_filter == "PENDING":
        queryset = queryset.filter(reconciliation__status=CorporateActionReconciliationStatus.PENDING, dismissed=False)
    elif status_filter == "RECORDED":
        queryset = queryset.filter(reconciliation__status=CorporateActionReconciliationStatus.ALREADY_RECORDED)
    elif status_filter == "COMPLETED":
        queryset = queryset.filter(reconciliation__status=CorporateActionReconciliationStatus.COMPLETED)
    elif status_filter == "CANCELLED":
        queryset = queryset.filter(action__status=CorporateActionStatus.REJECTED)
    page = max(1, int(request.query_params.get("page", 1) or 1))
    page_size = min(100, max(1, int(request.query_params.get("page_size", 25) or 25)))
    total = queryset.count()
    rows = queryset[(page - 1) * page_size:page * page_size]
    results = []
    for row in rows:
        action = row.action
        exposures = [{
            "type": exposure.exposure_type,
            "investment_name": exposure.investment_name,
            "investment_id": exposure.investment_id,
            "holding_weight": exposure.holding_weight,
            "snapshot_date": exposure.snapshot_date,
        } for exposure in action.exposures.filter(owner=request.user)]
        results.append({
            "id": row.id,
            "action": _serialize(action),
            "reconciliation": {
                "id": row.reconciliation_id,
                "status": row.reconciliation.status,
                "matched_transaction_ids": row.reconciliation.matched_transaction_ids,
                "suggested_quantity": row.reconciliation.suggested_quantity,
                "notes": row.reconciliation.notes,
            },
            "exposures": exposures,
            "reviewed": row.reviewed,
            "dismissed": row.dismissed,
            "direct_exposure": any(item["type"] == "DIRECT" for item in exposures),
        })
    return Response({"count": total, "page": page, "page_size": page_size, "results": results})


@api_view(["PATCH"])
@permission_classes([IsAuthenticated, CanWriteFinancialData])
def corporate_action_notification_update(request, notification_id):
    notification = update_notification_state(
        owner=request.user,
        notification_id=notification_id,
        reviewed=request.data.get("reviewed") if "reviewed" in request.data else None,
        dismissed=request.data.get("dismissed") if "dismissed" in request.data else None,
    )
    if notification is None:
        return Response({"detail": "Corporate action notification not found."}, status=404)
    return Response({
        "id": notification.id,
        "reviewed": notification.reviewed,
        "dismissed": notification.dismissed,
        "reconciliation_status": notification.reconciliation.status,
    })


@api_view(["POST"])
@permission_classes([IsAuthenticated, CanWriteFinancialData])
def corporate_action_reconciliation_update(request, reconciliation_id):
    reconciliation = CorporateActionReconciliation.objects.filter(
        owner=request.user, id=reconciliation_id
    ).select_related("action").first()
    if reconciliation is None:
        return Response({"detail": "Reconciliation not found."}, status=404)
    decision = str(request.data.get("decision", "")).upper()
    if decision not in {"ALREADY_RECORDED", "DISMISSED"}:
        return Response({"detail": "Supported decisions: ALREADY_RECORDED, DISMISSED."}, status=400)
    reconciliation.status = decision
    reconciliation.notes = str(request.data.get("notes", reconciliation.notes))[:5000]
    reconciliation.reviewed_at = timezone.now()
    reconciliation.reviewed_by = request.user
    reconciliation.save(update_fields=["status", "notes", "reviewed_at", "reviewed_by", "updated_at"])
    CorporateActionNotification.objects.filter(
        owner=request.user, action=reconciliation.action
    ).update(reviewed=True, dismissed=(decision == "DISMISSED"), updated_at=timezone.now())
    return Response({"id": reconciliation.id, "status": reconciliation.status})
