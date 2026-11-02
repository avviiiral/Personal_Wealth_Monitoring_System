import hashlib
import logging
from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db.models import Max, Q
from django.utils import timezone

from users.permissions import get_active_family_group_id
from users.models import FamilyMembership
from investments.models import Asset, Transaction, TransactionType, AssetUnderlyingHolding
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


logger = logging.getLogger(__name__)


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
    """Multiply confirmed ratio actions effective after the transaction and through valuation date."""
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
        if action.old_units and action.new_units:
            multiplier *= action.new_units / action.old_units
    return multiplier


def _idempotency_key(action_id, owner_id):
    return hashlib.sha256(f"corporate-action:{action_id}:owner:{owner_id}".encode()).hexdigest()


def _user_family_id(user):
    return get_active_family_group_id(user)


def _create_reconciliation(action, user, status_value, transaction_ids=None, suggested_quantity=None, notes=""):
    family_id = _user_family_id(user)
    reconciliation, _ = CorporateActionReconciliation.objects.get_or_create(
        action=action,
        owner=user,
        defaults={
            "family_id_snapshot": family_id,
            "status": status_value,
            "matched_transaction_ids": transaction_ids or [],
            "suggested_quantity": suggested_quantity,
            "notes": notes,
            "idempotency_key": _idempotency_key(action.id, user.id),
        },
    )
    if status_value in {
        CorporateActionReconciliationStatus.PENDING,
        CorporateActionReconciliationStatus.ALREADY_RECORDED,
        CorporateActionReconciliationStatus.COMPLETED,
    }:
        CorporateActionNotification.objects.get_or_create(
            action=action,
            owner=user,
            defaults={"reconciliation": reconciliation},
        )
    return reconciliation


def _direct_exposures(action):
    assets = Asset.objects.filter(is_active=True)
    if action.isin:
        assets = assets.filter(isin__iexact=action.isin)
    elif action.symbol:
        assets = assets.filter(symbol__iexact=action.symbol)
    else:
        return assets.none()
    return assets.select_related("owner", "family")


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


def _pms_underlying(action):
    return (
        AssetUnderlyingHolding.objects.filter(isin__iexact=action.isin).select_related("asset", "family")
        if action.isin else AssetUnderlyingHolding.objects.none()
    )


def discover_exposures_and_reconcile(action):
    """Map exact-ISIN direct/MF/PMS exposure and create idempotent per-user reconciliation state."""
    if not action.isin:
        return {"direct": 0, "mutual_fund": 0, "pms": 0, "notifications": 0}

    direct_count = mf_count = pms_count = notification_count = 0
    for asset in _direct_exposures(action).iterator():
        if asset.family_id:
            member_ids = FamilyMembership.objects.filter(
                family_group_id=asset.family_id
            ).values_list("profile__user_id", flat=True)
            users = get_user_model().objects.filter(id__in=member_ids, is_active=True)
        else:
            users = [asset.owner] if asset.owner_id and asset.owner.is_active else []

        for user in users:
            if user is None:
                continue
            family_id = asset.family_id or _user_family_id(user)
            exposure, _ = CorporateActionExposure.objects.get_or_create(
                action=action, owner=user, exposure_type="DIRECT",
                investment_name=asset.name, investment_id=asset.id,
                defaults={"family_id_snapshot": family_id, "snapshot_date": timezone.localdate()},
            )
            prior_transactions = Transaction.objects.filter(
                owner=user, asset=asset, transaction_date__lt=action.ex_date
            )
            if family_id:
                prior_transactions = prior_transactions.filter(family_id=family_id)
            expected_type = (
                TransactionType.DIVIDEND if action.action_type == "CASH_DIVIDEND"
                else TransactionType.BONUS if action.action_type in {"BONUS", "STOCK_DIVIDEND"}
                else TransactionType.SPLIT if action.action_type in {"STOCK_SPLIT", "REVERSE_SPLIT"}
                else None
            )
            eligible_quantity = None
            if expected_type is not None:
                existing = Transaction.objects.filter(
                    owner=user, asset=asset,
                    transaction_date__gte=action.ex_date,
                    transaction_type=expected_type,
                )
                if family_id:
                    existing = existing.filter(family_id=family_id)
            else:
                existing = Transaction.objects.none()

            if expected_type is not None and expected_type != TransactionType.DIVIDEND:
                from corporate_actions.position_adjustment import (
                    calculate_position_with_actions,
                    confirmed_ratio_actions,
                )
                tx_rows = list(prior_transactions.order_by("transaction_date", "created_at", "id"))
                if tx_rows:
                    totals = calculate_position_with_actions(
                        tx_rows,
                        confirmed_ratio_actions(asset=asset, owner=user, as_of_date=action.ex_date),
                    )
                    eligible_quantity = totals.get("quantity")

            if existing.exists():
                _create_reconciliation(
                    action, user, CorporateActionReconciliationStatus.ALREADY_RECORDED,
                    list(existing.values_list("id", flat=True)[:50]),
                )
            else:
                # Only create an actionable record where dated acquisition history exists.
                # Entitlement quantity still needs investor verification.
                status_value = (
                    CorporateActionReconciliationStatus.PENDING
                    if prior_transactions.exists()
                    else CorporateActionReconciliationStatus.MANUAL_REVIEW
                )
                if eligible_quantity is not None:
                    exposure.quantity = eligible_quantity
                    exposure.save(update_fields=["quantity"])
                _create_reconciliation(
                    action, user, status_value,
                    suggested_quantity=eligible_quantity,
                    notes="Direct security exposure found. Verify eligible holdings and the depository statement before booking.",
                )
                if status_value == CorporateActionReconciliationStatus.PENDING:
                    notification_count += 1
            direct_count += 1

    for underlying in _latest_mf_underlying(action).iterator():
        scheme = underlying.scheme
        fund_transactions = MutualFundTransaction.objects.filter(scheme=scheme)
        if scheme.family_id:
            fund_transactions = fund_transactions.filter(family_id=scheme.family_id)
        owner_ids = fund_transactions.exclude(owner__isnull=True).values_list("owner_id", flat=True).distinct()
        users = get_user_model().objects.filter(id__in=owner_ids, is_active=True)
        for user in users:
            quantity = None
            if underlying.quantity is not None:
                # Units are in the fund disclosure; retain them as underlying metadata only.
                quantity = underlying.quantity
            CorporateActionExposure.objects.get_or_create(
                action=action, owner=user, exposure_type="MUTUAL_FUND",
                investment_name=scheme.scheme_name, investment_id=scheme.id,
                defaults={
                    "family_id_snapshot": scheme.family_id or _user_family_id(user),
                    "quantity": quantity,
                    "holding_weight": underlying.percentage_of_nav,
                    "snapshot_date": underlying.portfolio_date,
                },
            )
            _create_reconciliation(
                action, user, CorporateActionReconciliationStatus.MANUAL_REVIEW,
                notes="Indirect mutual-fund underlying exposure only. Do not create a direct stock transaction.",
            )
            mf_count += 1

    for holding in _pms_underlying(action).iterator():
        member_ids = FamilyMembership.objects.filter(
            family_group_id=holding.family_id
        ).values_list("profile__user_id", flat=True)
        for user in get_user_model().objects.filter(id__in=member_ids, is_active=True):
            CorporateActionExposure.objects.get_or_create(
                action=action, owner=user, exposure_type="PMS",
                investment_name=holding.asset.name, investment_id=holding.asset_id,
                defaults={
                    "family_id_snapshot": holding.family_id,
                    "holding_weight": holding.holding_percentage,
                    "snapshot_date": timezone.localdate(),
                },
            )
            _create_reconciliation(
                action, user, CorporateActionReconciliationStatus.MANUAL_REVIEW,
                notes="Indirect PMS underlying exposure only. Review within the PMS statement; do not create a direct stock transaction.",
            )
            pms_count += 1

    return {
        "direct": direct_count,
        "mutual_fund": mf_count,
        "pms": pms_count,
        "notifications": notification_count,
    }


def reconcile_action_for_all_users(action):
    """Run for users exposed to this action; model constraints make repeats idempotent."""
    return discover_exposures_and_reconcile(action)


def update_notification_state(*, owner, notification_id, reviewed=None, dismissed=None):
    notification = CorporateActionNotification.objects.filter(owner=owner, id=notification_id).select_related("reconciliation").first()
    if notification is None:
        return None
    if reviewed is not None:
        notification.reviewed = bool(reviewed)
    if dismissed is not None:
        notification.dismissed = bool(dismissed)
    notification.save(update_fields=["reviewed", "dismissed", "updated_at"])
    if notification.dismissed:
        notification.reconciliation.status = CorporateActionReconciliationStatus.DISMISSED
        notification.reconciliation.reviewed_at = timezone.now()
        notification.reconciliation.reviewed_by = owner
        notification.reconciliation.save(update_fields=["status", "reviewed_at", "reviewed_by", "updated_at"])
    return notification
