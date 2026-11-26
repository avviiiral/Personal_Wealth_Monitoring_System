from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction as db_transaction
from django.db.models import Prefetch, Q
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from users.permissions import CanWriteFinancialData, is_admin_or_above
from users.models import FamilyMembership
from investments.models import Asset

from .models import (
    CorporateAction,
    CorporateActionStatus,
    CorporateActionNotification,
    CorporateActionPosition,
    CorporateActionReconciliation,
    CorporateActionReconciliationStatus,
)
from .reconciliation import (
    ReconciliationError,
    StaleProposalError,
    accept_reconciliation,
    is_terminal,
    refresh_reconciliation,
    reject_reconciliation,
)
from .services import resolve_corporate_action_candidates, update_notification_state, decide_missing_isin_reconciliation

ReconStatus = CorporateActionReconciliationStatus

EDITABLE_FIELDS = {
    "action_type", "security_name", "isin", "symbol", "exchange", "ex_date",
    "record_date", "payment_date", "old_units", "new_units", "cash_per_unit",
    "resulting_isin", "resulting_symbol", "resulting_security_name",
    "cost_allocation_ratio", "cash_in_lieu_price", "source", "source_reference",
    "status", "notes", "metadata",
}
# Changing these after a booking would make the ledger disagree with the event.
FINANCIAL_TERM_FIELDS = EDITABLE_FIELDS - {"status", "notes", "metadata", "source_reference"}
BOOKED_STATUSES = {ReconStatus.COMPLETED, ReconStatus.APPROVED}


def _serialize(action):
    return {
        "id": action.id, "action_type": action.action_type,
        "action_type_display": action.get_action_type_display(),
        "security_name": action.security_name, "isin": action.isin,
        "symbol": action.symbol, "exchange": action.exchange,
        "ex_date": action.ex_date, "record_date": action.record_date,
        "payment_date": action.payment_date, "old_units": action.old_units,
        "new_units": action.new_units, "cash_per_unit": action.cash_per_unit,
        "resulting_isin": action.resulting_isin, "resulting_symbol": action.resulting_symbol,
        "resulting_security_name": action.resulting_security_name,
        "cost_allocation_ratio": action.cost_allocation_ratio,
        "cash_in_lieu_price": action.cash_in_lieu_price,
        "source": action.source, "source_reference": action.source_reference,
        "status": action.status, "notes": action.notes, "metadata": action.metadata,
        "created_at": action.created_at, "updated_at": action.updated_at,
        "quantity_factor": action.quantity_factor,
    }


def _serialize_position(position):
    calculation = position.calculation or {}
    return {
        "id": position.id,
        "status": position.status,
        "family_id": position.family_id,
        "family_name": position.family_name,
        "portfolio": position.portfolio,
        "asset_class": position.asset_class,
        "sub_class": position.sub_class,
        "asset_name": position.asset_name,
        "underlying": position.underlying,
        "advisors": position.advisors,
        "asset_ids": position.asset_ids,
        "eligibility_cutoff": position.eligibility_cutoff,
        "eligible_quantity": position.eligible_quantity,
        "quantity_delta": position.quantity_delta,
        "resulting_quantity": position.resulting_quantity,
        "new_security_quantity": position.new_security_quantity,
        "fractional_entitlement": position.fractional_entitlement,
        "cost_basis": position.cost_basis,
        "resulting_cost_basis": position.resulting_cost_basis,
        "allocated_cost": position.allocated_cost,
        "cash_amount": position.cash_amount,
        "matched_transaction_ids": position.matched_transaction_ids,
        "matched_transactions": calculation.get("matched_transactions", []),
        "created_transaction_ids": position.created_transaction_ids,
        "planned_entries": position.planned_entries,
        "explanation": calculation.get("explanation", ""),
        "eligibility_rule": calculation.get("eligibility_rule", ""),
        "cash_in_lieu_pending": bool(calculation.get("cash_in_lieu_pending")),
        "reason": position.reason,
        "applied_at": position.applied_at,
    }


def _position_summary(positions):
    counts = {}
    for position in positions:
        counts[position["status"]] = counts.get(position["status"], 0) + 1
    actionable = [p for p in positions if p["status"] in ("PENDING", "APPLIED")]
    eligible = [p for p in positions if p["status"] != "NOT_ELIGIBLE"]

    def total(field, rows):
        values = [p[field] for p in rows if p[field] is not None]
        return sum(values) if values else None

    return {
        "status_counts": counts,
        "affected_portfolio_count": len({(p["family_id"], p["family_name"], p["portfolio"]) for p in eligible}),
        "affected_family_members": sorted({p["family_name"] for p in eligible if p["family_name"]}),
        "affected_asset_names": sorted({p["asset_name"] for p in eligible if p["asset_name"]}),
        "affected_asset_classes": sorted({p["asset_class"] for p in eligible if p["asset_class"]}),
        "original_quantity": total("eligible_quantity", actionable),
        "proposed_adjustment": total("quantity_delta", actionable),
        "resulting_quantity": total("resulting_quantity", actionable),
        "cash_amount": total("cash_amount", actionable),
        "already_recorded": counts.get("ALREADY_RECORDED", 0),
        "manual_review": counts.get("MANUAL_REVIEW", 0),
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

    payload = {key: value for key, value in request.data.items() if key in EDITABLE_FIELDS}
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
    booked = action.reconciliations.filter(status__in=BOOKED_STATUSES).exists()
    if request.method == "DELETE":
        if booked:
            return Response({"detail": "This corporate action is booked in the ledger and cannot be deleted."}, status=409)
        action.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    changes = {key: value for key, value in request.data.items() if key in EDITABLE_FIELDS}
    if booked and FINANCIAL_TERM_FIELDS & set(changes):
        return Response({
            "detail": "This corporate action is already booked in the ledger; its financial terms can no longer be changed.",
        }, status=409)
    for key, value in changes.items():
        setattr(action, key, value)
    try:
        with db_transaction.atomic():
            action.full_clean()
            if FINANCIAL_TERM_FIELDS & set(changes) and isinstance(action.metadata, dict):
                # The user has reviewed and corrected the terms.
                action.metadata = {k: v for k, v in action.metadata.items() if k != "term_conflicts"}
            action.save()
            # Verifying (or editing) an event never changes holdings by itself.
            # Only the owner's own, not yet decided, proposal is refreshed.
            for reconciliation in action.reconciliations.filter(owner=request.user):
                if not is_terminal(reconciliation):
                    refresh_reconciliation(reconciliation)
    except ValidationError as exc:
        return Response({"detail": exc.message_dict if hasattr(exc, "message_dict") else exc.messages}, status=400)
    except IntegrityError:
        return Response({"detail": "An action with this security, type and ex-date already exists."}, status=409)
    action.refresh_from_db()
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


def _notification_queryset(request):
    # Each user reviews their own copy of an event; copies owned by other
    # users (created by earlier versions of the scan) are not shown twice.
    queryset = CorporateActionNotification.objects.filter(
        owner=request.user, action__owner=request.user,
    ).select_related("action", "reconciliation", "reconciliation__reviewed_by").prefetch_related(
        "action__exposures",
        Prefetch("reconciliation__positions", queryset=CorporateActionPosition.objects.order_by(
            "family_name", "portfolio", "asset_class", "sub_class", "asset_name", "id")),
    )
    status_filter = request.query_params.get("status", "").upper()
    if status_filter == "NEW":
        queryset = queryset.filter(reviewed=False, dismissed=False)
    elif status_filter == "PENDING":
        queryset = queryset.filter(reconciliation__status=ReconStatus.PENDING, dismissed=False)
    elif status_filter == "IDENTITY":
        queryset = queryset.filter(reconciliation__status=ReconStatus.PENDING_USER_APPROVAL, dismissed=False)
    elif status_filter == "RECORDED":
        queryset = queryset.filter(reconciliation__status=ReconStatus.ALREADY_RECORDED)
    elif status_filter == "MANUAL":
        queryset = queryset.filter(reconciliation__status=ReconStatus.MANUAL_REVIEW)
    elif status_filter == "COMPLETED":
        queryset = queryset.filter(reconciliation__status__in=(ReconStatus.COMPLETED, ReconStatus.APPROVED))
    elif status_filter == "REJECTED":
        queryset = queryset.filter(reconciliation__status__in=(ReconStatus.REJECTED, ReconStatus.DISMISSED))
    elif status_filter == "CANCELLED":
        queryset = queryset.filter(action__status=CorporateActionStatus.REJECTED)
    return queryset.order_by("-action__ex_date", "-id")


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def corporate_action_notifications(request):
    queryset = _notification_queryset(request)
    try:
        page = max(1, int(request.query_params.get("page", 1) or 1))
        page_size = min(100, max(1, int(request.query_params.get("page_size", 25) or 25)))
    except (TypeError, ValueError):
        return Response({"detail": "page and page_size must be integers."}, status=400)
    total = queryset.count()
    rows = list(queryset[(page - 1) * page_size:page * page_size])
    visible_family_ids = FamilyMembership.objects.filter(
        profile__user=request.user
    ).values_list("family_group_id", flat=True)
    visible_candidate_scope = Q(owner=request.user) | Q(family_id__in=visible_family_ids)
    can_write = is_admin_or_above(request.user)
    results = []
    for row in rows:
        action = row.action
        reconciliation = row.reconciliation
        exposures = [{
            "type": exposure.exposure_type,
            "investment_name": exposure.investment_name,
            "investment_id": exposure.investment_id,
            "quantity": exposure.quantity,
            "holding_weight": exposure.holding_weight,
            "snapshot_date": exposure.snapshot_date,
        } for exposure in action.exposures.all() if exposure.owner_id == request.user.id]
        positions = [_serialize_position(position) for position in reconciliation.positions.all()]
        direct = any(item["type"] == "DIRECT" for item in exposures) or bool(positions)
        can_accept = (
            can_write and direct and reconciliation.status == ReconStatus.PENDING
            and action.status != CorporateActionStatus.REJECTED
            and any(p["status"] == "PENDING" for p in positions)
        )
        results.append({
            "id": row.id,
            "action": _serialize(action),
            "reconciliation": {
                "id": reconciliation.id,
                "status": reconciliation.status,
                "status_display": reconciliation.get_status_display(),
                "matched_transaction_ids": reconciliation.matched_transaction_ids,
                "suggested_quantity": reconciliation.suggested_quantity,
                "identity_match_method": reconciliation.identity_match_method,
                "candidate_assets": [
                    {
                        "id": asset.id,
                        "name": asset.name,
                        "symbol": asset.symbol or "",
                        "isin": asset.isin or "",
                        "exchange": getattr(asset, "exchange", "") or "",
                    }
                    for asset in Asset.objects.filter(
                        id__in=reconciliation.candidate_asset_ids,
                        is_active=True,
                    ).filter(visible_candidate_scope).order_by("name", "id")
                ] if reconciliation.candidate_asset_ids else [],
                "candidate_asset_ids": reconciliation.candidate_asset_ids,
                "selected_asset_id": reconciliation.selected_asset_id,
                "notes": reconciliation.notes,
                "proposal_fingerprint": reconciliation.proposal_fingerprint,
                "last_reconciled_at": reconciliation.last_reconciled_at,
                "reviewed_at": reconciliation.reviewed_at,
                "reviewed_by": reconciliation.reviewed_by.get_username() if reconciliation.reviewed_by else None,
                "positions": positions,
                "summary": _position_summary(positions),
                "can_accept": can_accept,
                "can_reject": can_write and reconciliation.status not in (
                    ReconStatus.COMPLETED, ReconStatus.APPROVED, ReconStatus.REJECTED,
                ),
            },
            "exposures": exposures,
            "reviewed": row.reviewed,
            "dismissed": row.dismissed,
            "direct_exposure": direct,
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
    if reconciliation.status in BOOKED_STATUSES or reconciliation.status == ReconStatus.REJECTED:
        return Response({"detail": "This corporate action has already been decided."}, status=409)
    reconciliation.status = decision
    reconciliation.notes = str(request.data.get("notes", reconciliation.notes))[:5000]
    reconciliation.reviewed_at = timezone.now()
    reconciliation.reviewed_by = request.user
    reconciliation.save(update_fields=["status", "notes", "reviewed_at", "reviewed_by", "updated_at"])
    CorporateActionNotification.objects.filter(
        owner=request.user, action=reconciliation.action
    ).update(reviewed=True, dismissed=(decision == "DISMISSED"), updated_at=timezone.now())
    return Response({"id": reconciliation.id, "status": reconciliation.status})


def _reconciliation_response(reconciliation, counters=None):
    payload = {
        "id": reconciliation.id,
        "status": reconciliation.status,
        "notes": reconciliation.notes,
        "matched_transaction_ids": reconciliation.matched_transaction_ids,
        "proposal_fingerprint": reconciliation.proposal_fingerprint,
        "reviewed_at": reconciliation.reviewed_at,
        "reviewed_by": reconciliation.reviewed_by.get_username() if reconciliation.reviewed_by else None,
    }
    if counters is not None:
        payload["created_transaction_ids"] = counters["created"]
        payload["reused_transaction_ids"] = counters["reused"]
        payload["already_recorded_transaction_ids"] = counters["recorded"]
    return payload


def _error(exc, code=400):
    return Response({"detail": exc.message_dict if hasattr(exc, "message_dict") else " ".join(exc.messages)}, status=code)


@api_view(["POST"])
@permission_classes([IsAuthenticated, CanWriteFinancialData])
def corporate_action_accept(request, reconciliation_id):
    """Accept a proposal: book the missing ledger rows and rebuild positions."""
    if request.data.get("confirm") is not True:
        return Response({"detail": "Set confirm=true to accept and book this corporate action."}, status=400)
    try:
        reconciliation, counters = accept_reconciliation(
            owner=request.user,
            reconciliation_id=reconciliation_id,
            fingerprint=str(request.data.get("proposal_fingerprint") or "") or None,
            notes=str(request.data.get("notes", "")),
        )
    except LookupError:
        return Response({"detail": "Reconciliation not found."}, status=404)
    except StaleProposalError as exc:
        return _error(exc, 409)
    except ReconciliationError as exc:
        return _error(exc, 400)
    return Response(_reconciliation_response(reconciliation, counters))


@api_view(["POST"])
@permission_classes([IsAuthenticated, CanWriteFinancialData])
def corporate_action_reject(request, reconciliation_id):
    """Reject a proposal with a reason. Never changes the ledger."""
    try:
        reconciliation = reject_reconciliation(
            owner=request.user, reconciliation_id=reconciliation_id,
            reason=str(request.data.get("reason", "")),
        )
    except LookupError:
        return Response({"detail": "Reconciliation not found."}, status=404)
    except ReconciliationError as exc:
        return _error(exc, 409)
    return Response(_reconciliation_response(reconciliation))


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def corporate_action_refresh(request, reconciliation_id):
    """Re-calculate the proposal against the current ledger. Never books anything."""
    reconciliation = CorporateActionReconciliation.objects.filter(
        owner=request.user, id=reconciliation_id
    ).select_related("action", "owner").first()
    if reconciliation is None:
        return Response({"detail": "Reconciliation not found."}, status=404)
    with db_transaction.atomic():
        refresh_reconciliation(reconciliation)
    reconciliation.refresh_from_db()
    return Response(_reconciliation_response(reconciliation))


@api_view(["POST"])
@permission_classes([IsAuthenticated, CanWriteFinancialData])
def corporate_action_identity_decision(request, reconciliation_id):
    decision = str(request.data.get("decision", "")).upper()
    try:
        reconciliation = decide_missing_isin_reconciliation(
            owner=request.user,
            reconciliation_id=reconciliation_id,
            decision=decision,
            asset_id=request.data.get("asset_id"),
            notes=str(request.data.get("notes", "")),
        )
    except LookupError:
        return Response({"detail": "Reconciliation not found."}, status=404)
    except ValidationError as exc:
        return Response({"detail": exc.message_dict if hasattr(exc, "message_dict") else exc.messages}, status=400)
    return Response({
        "id": reconciliation.id,
        "status": reconciliation.status,
        "selected_asset_id": reconciliation.selected_asset_id,
        "matched_transaction_ids": reconciliation.matched_transaction_ids,
        "notes": reconciliation.notes,
    })
