from datetime import date, datetime
from decimal import Decimal

from .models import CorporateAction, CorporateActionStatus, RATIO_ACTIONS


def resolve_corporate_action_candidates(*, owner, isin="", symbol="", exchange="", event_date=None):
    """
    Return exact-identifier candidates only. Names and price/quantity patterns
    are deliberately not sufficient to auto-identify a security or confirm an action.
    """
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
    """
    Product of confirmed quantity ratios effective after the transaction and
    on/before valuation date. This is a reusable calculation primitive; callers
    must ensure market prices use a compatible adjustment convention.
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
        if action.old_units and action.new_units:
            multiplier *= action.new_units / action.old_units
    return multiplier


import hashlib
import logging
from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.utils import timezone
from django.contrib.auth import get_user_model
from users.models import FamilyMember

from investments.models import Asset, Transaction, TransactionType
from mutual_funds.models import MutualFundScheme, MutualFundTransaction, MutualFundTransactionType
from mutual_funds.underlying_models import MutualFundUnderlying
from .models import (
    CorporateActionExposure,
    CorporateActionNotification,
    CorporateActionReconciliation,
    CorporateActionReconciliationStatus,
)

logger = logging.getLogger(__name__)


def _idempotency_key(action_id, owner_id):
    return hashlib.sha256(f"corporate-action:{action_id}:owner:{owner_id}".encode()).hexdigest()


def _user_family_id(user):
    member = FamilyMember.objects.filter(user=user, is_active=True).order_by("id").first()
    return member.family_id if member else None


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
    if status_value == CorporateActionReconciliationStatus.PENDING:
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
    snapshots = MutualFundUnderlying.objects.filter(isin__iexact=action.isin) if action.isin else MutualFundUnderlying.objects.none()
    latest = snapshots.order_by("-portfolio_date").values_list("portfolio_date", flat=True).first()
    if not latest:
        return snapshots.none()
    return snapshots.filter(portfolio_date=latest).select_related("scheme")


def discover_exposures_and_reconcile(action):
    """Map exact-ISIN direct and current MF underlying exposures; never convert indirect exposure into stock ownership."""
    if not action.isin:
        return {"direct": 0, "mutual_fund": 0, "notifications": 0}

    direct_count = mf_count = notification_count = 0
    for asset in _direct_exposures(action).iterator():
        users = [asset.owner] if asset.owner_id else []
        if asset.family_id:
            member_ids = FamilyMember.objects.filter(family_id=asset.family_id, is_active=True).values_list("user_id", flat=True)
            users = list(get_user_model().objects.filter(id__in=member_ids, is_active=True))
        for user in users:
            if not user or not user.is_active:
                continue
            family_id = asset.family_id
            CorporateActionExposure.objects.get_or_create(
                action=action, owner=user, exposure_type="DIRECT",
                investment_name=asset.name, investment_id=asset.id,
                defaults={"family_id_snapshot": family_id, "snapshot_date": timezone.localdate()},
            )
            txs = Transaction.objects.filter(owner=user, asset=asset, transaction_date__lt=action.ex_date)
            if family_id:
                txs = txs.filter(family_id=family_id)
            # Explicit matching event transaction means the action is likely already booked.
            expected_type = TransactionType.DIVIDEND if "DIVIDEND" in action.action_type else (
                TransactionType.BONUS if action.action_type in {"BONUS", "STOCK_DIVIDEND"} else TransactionType.SPLIT
            )
            matching = Transaction.objects.filter(
                owner=user, asset=asset, transaction_date__gte=action.ex_date,
                transaction_type=expected_type,
            )
            if family_id:
                matching = matching.filter(family_id=family_id)
            if matching.exists():
                ids = list(matching.values_list("id", flat=True)[:50])
                _create_reconciliation(action, user, CorporateActionReconciliationStatus.ALREADY_RECORDED, ids)
            else:
                held = txs.exists()
                # A dated BUY/SIP history is only a screening signal, not a proof of entitlement.
                # If no eligible quantity can be reconstructed safely, route to manual verification.
                target_status = CorporateActionReconciliationStatus.PENDING if held else CorporateActionReconciliationStatus.MANUAL_REVIEW
                reconciliation = _create_reconciliation(
                    action, user, target_status,
                    notes="Direct security exposure found. Verify eligible quantity and depository/corporate-action statement before recording.",
                )
                if target_status == CorporateActionReconciliationStatus.PENDING:
                    notification_count += 1
            direct_count += 1

    for underlying in _latest_mf_underlying(action).iterator():
        scheme = underlying.scheme
        family_id = scheme.family_id
        # Determine owners with actual scheme transactions in this family.
        transactions = MutualFundTransaction.objects.filter(scheme=scheme)
        if family_id:
            transactions = transactions.filter(family_id=family_id)
        owners = transactions.exclude(owner__isnull=True).values_list("owner_id", flat=True).distinct()
        for user in get_user_model().objects.filter(id__in=list(owners), is_active=True):
            CorporateActionExposure.objects.get_or_create(
                action=action, owner=user, exposure_type="MUTUAL_FUND",
                investment_name=scheme.scheme_name, investment_id=scheme.id,
                defaults={
                    "family_id_snapshot": family_id,
                    "holding_weight": underlying.percentage_of_nav,
                    "snapshot_date": underlying.portfolio_date,
                },
            )
            _create_reconciliation(
                action, user, CorporateActionReconciliationStatus.MANUAL_REVIEW,
                notes="Indirect mutual-fund underlying exposure only. Corporate action is reflected inside the fund; do not create a direct equity transaction.",
            )
            mf_count += 1
    return {"direct": direct_count, "mutual_fund": mf_count, "notifications": notification_count}


def reconcile_action_for_all_users(action):
    """Run for existing active users; writes are idempotent via unique constraints."""
    return discover_exposures_and_reconcile(action)


def update_notification_state(*, owner, notification_id, reviewed=None, dismissed=None):
    notification = CorporateActionNotification.objects.filter(owner=owner, id=notification_id).first()
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
