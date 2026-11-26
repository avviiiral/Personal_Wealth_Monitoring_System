import hashlib
import logging
from datetime import date
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction as db_transaction
from django.db.models import Max, Q
from django.utils import timezone

from users.permissions import get_active_family_group_id
from investments.models import Asset, AssetUnderlyingHolding
from mutual_funds.models import MutualFundTransaction
from mutual_funds.underlying_models import MutualFundUnderlying

from .models import (
    CorporateAction,
    CorporateActionStatus,
    CorporateActionExposure,
    CorporateActionNotification,
    CorporateActionReconciliation,
    CorporateActionReconciliationStatus,
    RATIO_ACTIONS,
)
from .announcement_terms import fill_terms_from_announcements
from .position_adjustment import ratio_quantity_factor
from . import reconciliation as ledger_reconciliation


logger = logging.getLogger(__name__)

ReconStatus = CorporateActionReconciliationStatus

# Event terms a later provider scan may fill in but never silently overwrite.
PROVIDER_TERM_FIELDS = (
    "old_units", "new_units", "cash_per_unit", "record_date", "payment_date",
    "resulting_isin", "resulting_symbol",
)


def resolve_corporate_action_candidates(*, owner, isin="", symbol="", exchange="", event_date=None):
    """Return exact-identifier candidates; names alone are never sufficient for matching."""
    isin = (isin or "").strip().upper()
    symbol = (symbol or "").strip().upper()
    exchange = (exchange or "").strip().upper()
    if not isin and not symbol:
        return CorporateAction.objects.none()

    query = CorporateAction.objects.filter(owner=owner)
    if isin:
        query = query.filter(isin__iexact=isin)
    else:
        query = query.filter(symbol__iexact=symbol)
        if exchange:
            query = query.filter(exchange__iexact=exchange)
    if event_date:
        if isinstance(event_date, str):
            event_date = date.fromisoformat(event_date)
        query = query.filter(ex_date__lte=event_date)
    return query.order_by("ex_date", "id")


def quantity_multiplier(*, owner, isin="", symbol="", exchange="", transaction_date, valuation_date):
    """What-if multiplier of confirmed ratio actions between two dates (informational only).

    Holdings never use this value; they change only through accepted ledger rows.
    """
    if valuation_date < transaction_date:
        return Decimal("1")
    actions = resolve_corporate_action_candidates(
        owner=owner, isin=isin, symbol=symbol, exchange=exchange, event_date=valuation_date
    ).filter(
        status=CorporateActionStatus.CONFIRMED,
        action_type__in=RATIO_ACTIONS,
        ex_date__gt=transaction_date,
        ex_date__lte=valuation_date,
    )
    multiplier = Decimal("1")
    for action in actions:
        factor = ratio_quantity_factor(action)
        if factor is not None:
            multiplier *= factor
    return multiplier


def _idempotency_key(action_id, owner_id):
    return hashlib.sha256(f"corporate-action:{action_id}:owner:{owner_id}".encode()).hexdigest()


def _user_family_id(user):
    return get_active_family_group_id(user)


def _get_reconciliation(action, user, *, status_value, notes="", identity_match_method=""):
    reconciliation, created = CorporateActionReconciliation.objects.get_or_create(
        action=action,
        owner=user,
        defaults={
            "family_id_snapshot": _user_family_id(user),
            "status": status_value,
            "notes": notes,
            "idempotency_key": _idempotency_key(action.id, user.id),
            "identity_match_method": identity_match_method,
        },
    )
    if identity_match_method and not reconciliation.identity_match_method:
        reconciliation.identity_match_method = identity_match_method
        reconciliation.save(update_fields=["identity_match_method", "updated_at"])
    return reconciliation, created


def _ensure_notification(action, user, reconciliation):
    notification, created = CorporateActionNotification.objects.get_or_create(
        action=action, owner=user, defaults={"reconciliation": reconciliation},
    )
    return created


def discover_symbol_name_candidates(*, action, user):
    """Candidate discovery only: exact symbol first, then exact full security name."""
    family_ids = ledger_reconciliation.owner_family_ids(user)
    assets = Asset.objects.filter(is_active=True, category="STOCK")
    if family_ids:
        assets = assets.filter(Q(owner=user) | Q(family_id__in=family_ids))
    else:
        assets = assets.filter(owner=user)
    if action.symbol:
        from corporate_actions.identity import symbol_q

        symbol_matches = list(assets.filter(symbol_q(action.symbol)).order_by("name", "id"))
        if symbol_matches:
            return symbol_matches, "SYMBOL"
    if action.security_name:
        name_matches = list(assets.filter(name__iexact=action.security_name.strip()).order_by("name", "id"))
        if name_matches:
            return name_matches, "NAME"
    return [], "NONE"


def _latest_mf_underlying(action):
    rows = MutualFundUnderlying.objects.filter(isin__iexact=action.isin) if action.isin else MutualFundUnderlying.objects.none()
    latest_by_scheme = rows.values("scheme_id").annotate(latest_date=Max("portfolio_date"))
    latest_filters = None
    for item in latest_by_scheme:
        condition = Q(scheme_id=item["scheme_id"], portfolio_date=item["latest_date"])
        latest_filters = condition if latest_filters is None else latest_filters | condition
    if latest_filters is None:
        return rows.none()
    return rows.filter(latest_filters).select_related("scheme")


def _indirect_exposures(action, user, family_ids, *, persist):
    """Mutual-fund and PMS underlying exposure of ``user``. Never produces a transaction."""
    mf_count = pms_count = 0
    if not action.isin:
        return mf_count, pms_count
    for underlying in _latest_mf_underlying(action).iterator():
        scheme = underlying.scheme
        held = MutualFundTransaction.objects.filter(scheme=scheme).filter(
            Q(owner=user) | Q(family_id__in=family_ids)
        ).exists()
        if not held:
            continue
        mf_count += 1
        if persist:
            CorporateActionExposure.objects.get_or_create(
                action=action, owner=user, exposure_type="MUTUAL_FUND",
                investment_name=scheme.scheme_name, investment_id=scheme.id,
                defaults={
                    "family_id_snapshot": scheme.family_id or _user_family_id(user),
                    # Units are the fund's disclosure; kept as underlying metadata only.
                    "quantity": underlying.quantity,
                    "holding_weight": underlying.percentage_of_nav,
                    "snapshot_date": underlying.portfolio_date,
                },
            )
    if family_ids:
        holdings = AssetUnderlyingHolding.objects.filter(
            isin__iexact=action.isin, family_id__in=family_ids,
        ).select_related("asset")
        for holding in holdings.iterator():
            pms_count += 1
            if persist:
                CorporateActionExposure.objects.get_or_create(
                    action=action, owner=user, exposure_type="PMS",
                    investment_name=holding.asset.name, investment_id=holding.asset_id,
                    defaults={
                        "family_id_snapshot": holding.family_id,
                        "holding_weight": holding.holding_percentage,
                        "snapshot_date": timezone.localdate(),
                    },
                )
    return mf_count, pms_count


def _discover_without_isin(action, user):
    """Missing source ISIN: exact symbol/name candidates need explicit identity approval."""
    candidates, method = discover_symbol_name_candidates(action=action, user=user)
    if not candidates:
        return {"direct": 0, "mutual_fund": 0, "pms": 0, "notifications": 0, "status": None}
    notes = (
        f"Source ISIN unavailable. Exact {method.lower()} candidate(s) require your approval. "
        "No transaction has been changed."
    )
    reconciliation, _ = _get_reconciliation(
        action, user, status_value=ReconStatus.PENDING_USER_APPROVAL, notes=notes,
    )
    if reconciliation.status in {ReconStatus.REJECTED, ReconStatus.APPROVED} or ledger_reconciliation.is_terminal(reconciliation):
        # An explicit decision is terminal; later monitoring runs must not reopen it.
        return {"direct": 0, "mutual_fund": 0, "pms": 0, "notifications": 0, "status": reconciliation.status}
    if reconciliation.selected_asset_id and reconciliation.status != ReconStatus.PENDING_USER_APPROVAL:
        # Identity already approved (a buyback waiting for its accepted quantity): keep it, recalculate.
        ledger_reconciliation.refresh_reconciliation(reconciliation)
        return {"direct": 0, "mutual_fund": 0, "pms": 0, "notifications": 0, "status": reconciliation.status}
    reconciliation.status = ReconStatus.PENDING_USER_APPROVAL
    reconciliation.identity_match_method = method
    reconciliation.candidate_asset_ids = [asset.id for asset in candidates]
    reconciliation.notes = notes
    reconciliation.save(update_fields=["status", "identity_match_method", "candidate_asset_ids", "notes", "updated_at"])
    created = _ensure_notification(action, user, reconciliation)
    for asset in candidates:
        CorporateActionExposure.objects.get_or_create(
            action=action, owner=user, exposure_type="DIRECT",
            investment_name=asset.name, investment_id=asset.id,
            defaults={"family_id_snapshot": asset.family_id or _user_family_id(user),
                      "snapshot_date": timezone.localdate()},
        )
    return {"direct": len(candidates), "mutual_fund": 0, "pms": 0,
            "notifications": int(created), "status": reconciliation.status}


def discover_exposures_and_reconcile(action, *, persist_if_irrelevant=True):
    """Reconcile ``action`` for its owner: detection only, never a ledger write.

    Each user owns their own copy of an event (``CorporateAction.owner``), so a
    reconciliation is created only for that user, covering the families they
    belong to and their individual ledger. Returns counters.
    """
    user = action.owner
    if user is None or not user.is_active:
        return {"direct": 0, "mutual_fund": 0, "pms": 0, "notifications": 0, "status": None}
    if not action.isin:
        return _discover_without_isin(action, user)

    family_ids = ledger_reconciliation.owner_family_ids(user)
    existing = CorporateActionReconciliation.objects.filter(action=action, owner=user).first()
    if existing is not None and ledger_reconciliation.is_terminal(existing):
        return {"direct": 0, "mutual_fund": 0, "pms": 0, "notifications": 0, "status": existing.status}

    # Merger / demerger / buyback terms the feed did not give: read them from announcements.
    fill_terms_from_announcements(action)
    plan = ledger_reconciliation.build_plan(action, user)
    mf_count, pms_count = _indirect_exposures(action, user, family_ids, persist=False)
    has_indirect = bool(mf_count or pms_count)
    relevant = any(p.status != "NOT_ELIGIBLE" for p in plan.positions) or has_indirect
    if not plan.positions and not has_indirect and existing is None:
        return {"direct": 0, "mutual_fund": 0, "pms": 0, "notifications": 0, "status": None}
    if not relevant and existing is None and not persist_if_irrelevant:
        return {"direct": 0, "mutual_fund": 0, "pms": 0, "notifications": 0, "status": None, "irrelevant": True}

    with db_transaction.atomic():
        _indirect_exposures(action, user, family_ids, persist=True)
        quantities = {}
        for position in plan.positions:
            for leg in position.legs:
                quantities[leg.asset.id] = quantities.get(leg.asset.id, 0) + leg.eligible["quantity"]
        for asset in plan.identity_assets:
            if asset.id not in quantities:
                continue
            exposure, _ = CorporateActionExposure.objects.get_or_create(
                action=action, owner=user, exposure_type="DIRECT",
                investment_name=asset.name, investment_id=asset.id,
                defaults={"family_id_snapshot": asset.family_id or _user_family_id(user),
                          "snapshot_date": timezone.localdate()},
            )
            if exposure.quantity != quantities[asset.id]:
                exposure.quantity = quantities[asset.id]
                exposure.save(update_fields=["quantity"])
        isin_match_method = str((action.metadata or {}).get("identity_match_method") or "")[:24]
        reconciliation, _ = _get_reconciliation(
            action, user, status_value=ReconStatus.PENDING, identity_match_method=isin_match_method,
        )
        status = ledger_reconciliation.persist_plan(reconciliation, plan, has_indirect=has_indirect)
        created_notification = False
        if ledger_reconciliation.should_notify(plan, status, has_indirect=has_indirect):
            created_notification = _ensure_notification(action, user, reconciliation)

    return {
        "direct": sum(len(p.legs) for p in plan.positions),
        "mutual_fund": mf_count,
        "pms": pms_count,
        "notifications": int(created_notification),
        "status": status,
        "positions": len(plan.positions),
        "eligible_positions": sum(1 for p in plan.positions if p.status in ("PENDING", "ALREADY_RECORDED", "MANUAL_REVIEW")),
        "already_recorded": plan.count("ALREADY_RECORDED"),
        "manual_review": plan.count("MANUAL_REVIEW"),
        "pending": plan.count("PENDING"),
        "transactions_checked": plan.transactions_checked,
    }


def reconcile_action_for_all_users(action):
    """Reconcile the action for its owner. Each exposed user has their own action copy."""
    return discover_exposures_and_reconcile(action)


def upsert_corporate_action(*, owner, lookup, defaults):
    """Create or refresh one owner's copy of a discovered event.

    A later scan never resets the verification status or the user's review,
    and never overwrites stored terms: blank terms are filled, and terms that
    disagree with the provider are recorded in ``metadata.term_conflicts`` so
    the reconciliation goes to manual review instead of booking stale terms.
    Returns (action, created).
    """
    lookup = {key: value for key, value in lookup.items() if key != "owner"}
    action = CorporateAction.objects.filter(owner=owner, **lookup).first()
    if action is None:
        action = CorporateAction.objects.create(owner=owner, **{**defaults, **lookup})
        action.refresh_from_db()
        return action, True
    changed = []
    metadata = dict(action.metadata or {})
    conflicts = dict(metadata.get("term_conflicts") or {})
    for field_name in PROVIDER_TERM_FIELDS:
        incoming = defaults.get(field_name)
        if incoming in (None, ""):
            continue
        current = getattr(action, field_name)
        field = CorporateAction._meta.get_field(field_name)
        incoming_value = field.to_python(incoming)
        if current in (None, ""):
            setattr(action, field_name, incoming_value)
            changed.append(field_name)
        elif current != incoming_value:
            conflicts[field_name] = {"stored": str(current), "provider": str(incoming_value)}
    for field_name in ("source", "source_reference", "exchange"):
        incoming = defaults.get(field_name)
        if incoming and not getattr(action, field_name):
            setattr(action, field_name, incoming)
            changed.append(field_name)
    provider_metadata = defaults.get("metadata") or {}
    for key, value in provider_metadata.items():
        if key not in metadata or metadata.get(key) in (None, "", {}):
            metadata[key] = value
    if conflicts:
        metadata["term_conflicts"] = conflicts
    if metadata != (action.metadata or {}):
        action.metadata = metadata
        changed.append("metadata")
    if changed:
        action.save(update_fields=sorted(set(changed)) + ["updated_at"])
    action.refresh_from_db()
    return action, False


def update_notification_state(*, owner, notification_id, reviewed=None, dismissed=None):
    notification = CorporateActionNotification.objects.filter(owner=owner, id=notification_id).select_related("reconciliation").first()
    if notification is None:
        return None
    if reviewed is not None:
        notification.reviewed = bool(reviewed)
    if dismissed is not None:
        notification.dismissed = bool(dismissed)
    notification.save(update_fields=["reviewed", "dismissed", "updated_at"])
    reconciliation = notification.reconciliation
    if notification.dismissed and not ledger_reconciliation.is_terminal(reconciliation):
        # Dismissing never overrides a booked, approved or rejected decision.
        reconciliation.status = ReconStatus.DISMISSED
        reconciliation.reviewed_at = timezone.now()
        reconciliation.reviewed_by = owner
        reconciliation.save(update_fields=["status", "reviewed_at", "reviewed_by", "updated_at"])
    return notification


def decide_missing_isin_reconciliation(*, owner, reconciliation_id, decision, asset_id=None, notes=""):
    """Approve/reject a proposed identity without duplicating existing transaction rows."""
    with db_transaction.atomic():
        reconciliation = CorporateActionReconciliation.objects.select_for_update().select_related("action").filter(
            owner=owner, id=reconciliation_id
        ).first()
        if reconciliation is None:
            raise LookupError("Reconciliation not found.")
        if reconciliation.status == ReconStatus.REJECTED:
            return reconciliation
        if reconciliation.status == ReconStatus.APPROVED:
            # Only an idempotent retry for the originally approved asset is accepted.
            if decision != "APPROVE" or asset_id != reconciliation.selected_asset_id:
                raise ValidationError("This identity has already been approved for a different holding.")
            return reconciliation
        if reconciliation.status != ReconStatus.PENDING_USER_APPROVAL:
            raise ValidationError("This action is not awaiting identity approval.")

        action = reconciliation.action
        if decision == "REJECT":
            reconciliation.status = ReconStatus.REJECTED
            reconciliation.notes = (
                notes or "User rejected the proposed identity. No transaction was changed."
            )[:5000]
            reconciliation.positions.exclude(status="APPLIED").update(status="REJECTED")
        elif decision == "APPROVE":
            if asset_id not in reconciliation.candidate_asset_ids:
                raise ValidationError("Choose one of the proposed holdings.")
            family_ids = ledger_reconciliation.owner_family_ids(owner)
            identity_scope = Q(owner=owner)
            if family_ids:
                identity_scope |= Q(family_id__in=family_ids)
            asset = Asset.objects.select_for_update().filter(
                id=asset_id, is_active=True, category="STOCK"
            ).filter(identity_scope).first()
            if asset is None:
                raise ValidationError("Selected holding is no longer available.")
            if action.status == CorporateActionStatus.REJECTED:
                raise ValidationError("This event is marked rejected/cancelled; nothing can be booked for it.")
            reconciliation.family_id_snapshot = asset.family_id
            reconciliation.selected_asset = asset
            outcome = ledger_reconciliation.apply_identity_approval(
                owner=owner, reconciliation=reconciliation, asset=asset,
            )
            if outcome is None:
                # Buyback / tender offer: now an ordinary proposal awaiting the accepted
                # quantity (apply_identity_approval stored its status and positions).
                reconciliation.notes = (notes or (
                    "Identity approved. Enter how many shares the company accepted to book the sale, "
                    "or choose \"Did not tender\". Nothing has been booked yet."
                ))[:5000]
                reconciliation.save(update_fields=["selected_asset", "family_id_snapshot", "notes", "updated_at"])
                return reconciliation
            matched_ids, created_count, skipped_count = outcome
            reconciliation.matched_transaction_ids = matched_ids
            reconciliation.status = ReconStatus.APPROVED
            if notes:
                reconciliation.notes = notes[:5000]
            elif created_count == 0:
                reconciliation.notes = (
                    "Identity approved. Matching same-day transactions already existed for the "
                    "eligible hierarchy; no existing transaction was modified and no duplicate was created."
                )
            elif skipped_count:
                reconciliation.notes = (
                    f"Identity approved. Created {created_count} missing transaction(s) and skipped "
                    f"{skipped_count} same-day duplicate(s); existing transactions were not modified."
                )
            else:
                reconciliation.notes = (
                    f"Identity approved; created {created_count} transaction(s) idempotently."
                )
        else:
            raise ValidationError("Decision must be APPROVE or REJECT.")

        reconciliation.reviewed_at = timezone.now()
        reconciliation.reviewed_by = owner
        reconciliation.save()
        CorporateActionNotification.objects.filter(owner=owner, action=action).update(
            reviewed=True, dismissed=(decision == "REJECT"), updated_at=timezone.now()
        )
        return reconciliation
