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
