import hashlib
import logging
from datetime import date
from decimal import Decimal, ROUND_FLOOR

from django.contrib.auth import get_user_model
from django.db.models import Max, Q
from django.db import transaction as db_transaction
from django.core.exceptions import ValidationError
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
        CorporateActionReconciliationStatus.PENDING_USER_APPROVAL,
        CorporateActionReconciliationStatus.ALREADY_RECORDED,
        CorporateActionReconciliationStatus.COMPLETED,
    }:
        CorporateActionNotification.objects.get_or_create(
            action=action,
            owner=user,
            defaults={"reconciliation": reconciliation},
        )
    return reconciliation


def discover_symbol_name_candidates(*, action, user):
    """Candidate discovery only: exact symbol first, then exact full security name."""
    family_id = _user_family_id(user)
    assets = Asset.objects.filter(is_active=True, category="STOCK")
    if family_id:
        member_family_ids = list(
            FamilyMembership.objects.filter(profile__user=user).values_list("family_group_id", flat=True)
        )
        assets = assets.filter(Q(owner=user) | Q(family_id__in=member_family_ids))
    else:
        assets = assets.filter(owner=user)
    if action.symbol:
        symbol_matches = list(assets.filter(symbol__iexact=action.symbol.strip()).order_by("name", "id"))
        if symbol_matches:
            return symbol_matches, "SYMBOL"
    if action.security_name:
        name_matches = list(assets.filter(name__iexact=action.security_name.strip()).order_by("name", "id"))
        if name_matches:
            return name_matches, "NAME"
    return [], "NONE"


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
        direct_count = notification_count = 0
        candidate_query = Asset.objects.filter(is_active=True, category="STOCK")
        identity_query = Q()
        if action.symbol:
            identity_query |= Q(symbol__iexact=action.symbol.strip())
        if action.security_name:
            identity_query |= Q(name__iexact=action.security_name.strip())
        candidate_query = candidate_query.filter(identity_query)
        candidate_owner_ids = set(candidate_query.exclude(owner__isnull=True).values_list("owner_id", flat=True))
        family_ids = set(candidate_query.exclude(family_id__isnull=True).values_list("family_id", flat=True))
        member_ids = FamilyMembership.objects.filter(
            family_group_id__in=family_ids
        ).values_list("profile__user_id", flat=True)
        candidate_owner_ids.update(member_ids)
        for user in get_user_model().objects.filter(is_active=True, id__in=candidate_owner_ids).iterator():
            candidates, method = discover_symbol_name_candidates(action=action, user=user)
            if not candidates:
                continue
            reconciliation = _create_reconciliation(
                action, user, CorporateActionReconciliationStatus.PENDING_USER_APPROVAL,
                notes=f"Source ISIN unavailable. Exact {method.lower()} candidate(s) require your approval. No transaction has been changed.",
            )
            if reconciliation.status == CorporateActionReconciliationStatus.REJECTED:
                # An explicit rejection is terminal; later monitoring runs must not reopen it.
                continue
            if reconciliation.status == CorporateActionReconciliationStatus.APPROVED:
                # An approved identity is terminal; do not refresh candidate IDs or create a second transaction.
                continue
            # Populate identity fields on first creation as well as on a refreshed
            # pending reconciliation. get_or_create() has already set the status.
            reconciliation.status = CorporateActionReconciliationStatus.PENDING_USER_APPROVAL
            reconciliation.identity_match_method = method
            reconciliation.candidate_asset_ids = [asset.id for asset in candidates]
            reconciliation.notes = (
                f"Source ISIN unavailable. Exact {method.lower()} candidate(s) require your approval. "
                "No transaction has been changed."
            )
            reconciliation.save(update_fields=[
                "status", "identity_match_method", "candidate_asset_ids", "notes", "updated_at"
            ])
            CorporateActionNotification.objects.get_or_create(
                action=action, owner=user, defaults={"reconciliation": reconciliation}
            )
            for asset in candidates:
                CorporateActionExposure.objects.get_or_create(
                    action=action, owner=user, exposure_type="DIRECT",
                    investment_name=asset.name, investment_id=asset.id,
                    defaults={"family_id_snapshot": asset.family_id or _user_family_id(user),
                              "snapshot_date": timezone.localdate()},
                )
            direct_count += len(candidates)
            notification_count += 1
        return {"direct": direct_count, "mutual_fund": 0, "pms": 0, "notifications": notification_count}

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
            # Compute pre-event eligible units for every direct action type,
            # including dividends. Cash dividends use this quantity for an
            # entitlement estimate but never change share quantity.
            from corporate_actions.position_adjustment import (
                calculate_position_with_actions,
                confirmed_ratio_actions,
            )
            tx_rows = list(prior_transactions.order_by("transaction_date", "created_at", "id"))
            eligible_quantity = None
            if tx_rows:
                earlier_actions = confirmed_ratio_actions(asset=asset, owner=user)
                earlier_actions = [item for item in earlier_actions if item.ex_date < action.ex_date]
                totals = calculate_position_with_actions(tx_rows, earlier_actions)
                eligible_quantity = totals.get("quantity")

            if existing.exists():
                _create_reconciliation(
                    action, user, CorporateActionReconciliationStatus.ALREADY_RECORDED,
                    list(existing.values_list("id", flat=True)[:50]),
                )
            else:
                # Only create an actionable record where dated acquisition history exists.
                # Entitlement quantity still needs investor verification.
                # A transaction history alone does not establish ex-date entitlement.
                if eligible_quantity is not None and eligible_quantity > Decimal("0"):
                    exposure.quantity = eligible_quantity
                    exposure.save(update_fields=["quantity"])
                    _create_reconciliation(
                        action, user, CorporateActionReconciliationStatus.PENDING,
                        suggested_quantity=eligible_quantity,
                        notes="Positive direct holding calculated immediately before the ex-date. Verify the official entitlement before confirming the event.",
                    )
                    notification_count += 1
                else:
                    _create_reconciliation(
                        action, user, CorporateActionReconciliationStatus.MANUAL_REVIEW,
                        suggested_quantity=eligible_quantity,
                        notes="No positive eligible direct holding was established immediately before the ex-date. No automatic position adjustment will be applied for this investor.",
                    )
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


def decide_missing_isin_reconciliation(*, owner, reconciliation_id, decision, asset_id=None, notes=""):
    """Approve/reject a proposed identity; transaction writes occur only on explicit approval."""
    from investments.models import Transaction, TransactionSource, TransactionType

    with db_transaction.atomic():
        reconciliation = CorporateActionReconciliation.objects.select_for_update().select_related("action").filter(
            owner=owner, id=reconciliation_id
        ).first()
        if reconciliation is None:
            raise LookupError("Reconciliation not found.")
        if reconciliation.status == CorporateActionReconciliationStatus.REJECTED:
            return reconciliation
        if reconciliation.status == CorporateActionReconciliationStatus.APPROVED:
            # Only an idempotent retry for the originally approved asset is accepted.
            if decision != "APPROVE" or asset_id != reconciliation.selected_asset_id:
                raise ValidationError("This identity has already been approved for a different holding.")
            return reconciliation
        if reconciliation.status != CorporateActionReconciliationStatus.PENDING_USER_APPROVAL:
            raise ValidationError("This action is not awaiting identity approval.")
        action = reconciliation.action
        if decision == "REJECT":
            reconciliation.status = CorporateActionReconciliationStatus.REJECTED
            reconciliation.notes = (notes or "User rejected the proposed identity. No transaction was changed.")[:5000]
        elif decision == "APPROVE":
            if asset_id not in reconciliation.candidate_asset_ids:
                raise ValidationError("Choose one of the proposed holdings.")
            family_id = _user_family_id(owner)
            identity_scope = Q(owner=owner)
            member_family_ids = list(
                FamilyMembership.objects.filter(profile__user=owner).values_list("family_group_id", flat=True)
            )
            if member_family_ids:
                identity_scope |= Q(family_id__in=member_family_ids)
            asset_query = Asset.objects.select_for_update().filter(
                id=asset_id, is_active=True, category="STOCK"
            ).filter(identity_scope)
            asset = asset_query.first()
            if asset is not None:
                family_id = asset.family_id
                reconciliation.family_id_snapshot = family_id
            if asset is None:
                raise ValidationError("Selected holding is no longer available.")
            transaction_type = (
                TransactionType.DIVIDEND if action.action_type == "CASH_DIVIDEND"
                else TransactionType.BONUS if action.action_type in {"BONUS", "STOCK_DIVIDEND"}
                else TransactionType.SPLIT if action.action_type in {"STOCK_SPLIT", "REVERSE_SPLIT"}
                else None
            )
            if transaction_type is None:
                raise ValidationError("This action type cannot be converted into a transaction automatically.")
            if not action.ex_date:
                raise ValidationError("Action ex-date is required.")
            quantity = reconciliation.suggested_quantity
            if not quantity or quantity <= 0:
                from corporate_actions.position_adjustment import calculate_position_with_actions, confirmed_ratio_actions
                transactions = list(Transaction.objects.filter(
                    owner=owner, asset=asset, transaction_date__lt=action.ex_date
                ).filter(Q(family_id=asset.family_id) if asset.family_id else Q(family__isnull=True))
                 .order_by("transaction_date", "created_at", "id"))
                earlier = [
                    item for item in confirmed_ratio_actions(asset=asset, owner=owner)
                    if item.ex_date < action.ex_date
                ]
                quantity = calculate_position_with_actions(transactions, earlier).get("quantity")
            if not quantity or quantity <= 0:
                raise ValidationError("Could not establish a positive eligible quantity.")
            if transaction_type == TransactionType.DIVIDEND:
                price = action.cash_per_unit or Decimal("0")
                tx_quantity = quantity
                amount = quantity * price
            else:
                if not action.old_units or not action.new_units:
                    raise ValidationError("A verified entitlement ratio is required.")
                if transaction_type == TransactionType.BONUS:
                    # Bonus ratio is bonus shares per existing shares.
                    # NSE 1:2 means one whole bonus share for every two
                    # eligible shares. Fractional entitlements are discarded.
                    eligible_lots = (quantity / action.old_units).to_integral_value(
                        rounding=ROUND_FLOOR
                    )
                    tx_quantity = eligible_lots * action.new_units
                else:
                    # Split ratios represent the post-action total share
                    # multiplier, so record only the incremental quantity.
                    tx_quantity = quantity * (
                        action.new_units / action.old_units - Decimal("1")
                    )
                if tx_quantity <= 0:
                    raise ValidationError("Calculated entitlement is not positive.")
                price = Decimal("0")
                amount = Decimal("0")
            created_transactions = []
            if transaction_type == TransactionType.BONUS:
                # Preserve every existing portfolio hierarchy bucket. A single
                # asset can be held under multiple family members / portfolios /
                # asset classes, so entitlement must be calculated per bucket.
                from collections import defaultdict
                from corporate_actions.position_adjustment import (
                    calculate_position_with_actions,
                    confirmed_ratio_actions,
                )

                # Bonus eligibility is evaluated on the official record date,
                # across every Asset record for this security in the selected family.
                if not action.record_date:
                    raise ValidationError("Official record date is required to allocate bonus shares.")

                from collections import defaultdict
                from corporate_actions.position_adjustment import (
                    calculate_position_with_actions,
                    confirmed_ratio_actions,
                )

                hierarchy_fields = (
                    "family_name", "portfolio", "asset_class", "sub_class",
                    "asset_name", "underlying", "advisors",
                )
                matching_assets = Asset.objects.select_for_update().filter(
                    is_active=True, category="STOCK",
                )
                if asset.family_id:
                    matching_assets = matching_assets.filter(family_id=asset.family_id)
                else:
                    matching_assets = matching_assets.filter(owner=owner, family__isnull=True)
                if asset.isin:
                    matching_assets = matching_assets.filter(isin__iexact=asset.isin)
                elif asset.symbol:
                    matching_assets = matching_assets.filter(symbol__iexact=asset.symbol)
                else:
                    matching_assets = matching_assets.filter(pk=asset.pk)
                matching_asset_ids = list(matching_assets.values_list("id", flat=True))
                if asset.id not in matching_asset_ids:
                    matching_asset_ids.append(asset.id)

                prior_rows = Transaction.objects.filter(
                    asset_id__in=matching_asset_ids, transaction_date__lte=action.record_date,
                )
                if asset.family_id:
                    prior_rows = prior_rows.filter(family_id=asset.family_id)
                else:
                    prior_rows = prior_rows.filter(owner=owner, family__isnull=True)
                prior_rows = list(prior_rows.select_related("asset").order_by(
                    "transaction_date", "created_at", "id"
                ))

                grouped_rows = defaultdict(list)
                for prior in prior_rows:
                    values = {
                        field: (getattr(prior, field) or "").strip()
                        for field in hierarchy_fields
                    }
                    if not values["asset_name"]:
                        values["asset_name"] = prior.asset.name or asset.name
                    if not values["underlying"]:
                        values["underlying"] = values["asset_name"]
                    grouped_rows[tuple(values[field] for field in hierarchy_fields)].append(prior)

                for bucket, bucket_transactions in grouped_rows.items():
                    # Reconstruct each Asset-specific position, then aggregate
                    # within this exact hierarchy path. This avoids mixing
                    # unrelated family/portfolio/class/subclass occurrences.
                    rows_by_asset = defaultdict(list)
                    for prior in bucket_transactions:
                        rows_by_asset[prior.asset_id].append(prior)
                    bucket_quantity = Decimal("0")
                    for prior_asset_id, asset_rows in rows_by_asset.items():
                        prior_asset = asset_rows[0].asset
                        earlier_actions = [
                            item for item in confirmed_ratio_actions(asset=prior_asset, owner=owner)
                            if item.ex_date <= action.record_date
                        ]
                        position = calculate_position_with_actions(asset_rows, earlier_actions)
                        bucket_quantity += position.get("quantity") or Decimal("0")
                    if bucket_quantity <= 0:
                        continue
                    eligible_lots = (bucket_quantity / action.old_units).to_integral_value(
                        rounding=ROUND_FLOOR
                    )
                    bucket_bonus = eligible_lots * action.new_units
                    if bucket_bonus <= 0:
                        continue
                    bucket_identity = "|".join(str(value) for value in bucket)
                    key = hashlib.sha256(
                        f"corporate-action:{action.id}:owner:{owner.id}:family:{asset.family_id}:bucket:{bucket_identity}".encode()
                    ).hexdigest()
                    representative_asset = bucket_transactions[0].asset
                    defaults = {
                        "owner": owner, "family": representative_asset.family, "asset": representative_asset,
                        "transaction_type": transaction_type, "transaction_date": action.ex_date,
                        "quantity": bucket_bonus, "price_per_unit": Decimal("0"),
                        "amount": Decimal("0"), "fees": Decimal("0"),
                        "notes": f"User-approved corporate action #{action.id}; record-date allocation; identity matched by {reconciliation.identity_match_method}.",
                    }
                    defaults.update(dict(zip(hierarchy_fields, bucket)))
                    transaction, _ = Transaction.objects.get_or_create(
                        family=representative_asset.family, source=TransactionSource.OTHER,
                        source_key=key, defaults=defaults,
                    )
                    created_transactions.append(transaction)
                if not created_transactions:
                    raise ValidationError("No positive whole-share bonus entitlement was found across the record-date portfolio hierarchy.")
            else:
                key = hashlib.sha256(
                    f"corporate-action:{action.id}:owner:{owner.id}:asset:{asset.id}".encode()
                ).hexdigest()
                transaction, _ = Transaction.objects.get_or_create(
                    family=asset.family, source=TransactionSource.OTHER, source_key=key,
                    defaults={
                        "owner": owner, "family": asset.family, "asset": asset,
                        "transaction_type": transaction_type, "transaction_date": action.ex_date,
                        "quantity": tx_quantity, "price_per_unit": price, "amount": amount,
                        "fees": Decimal("0"),
                        "notes": f"User-approved corporate action #{action.id}; identity matched by {reconciliation.identity_match_method}.",
                    },
                )
                created_transactions.append(transaction)
            reconciliation.selected_asset = asset
            reconciliation.matched_transaction_ids = [item.id for item in created_transactions]
            reconciliation.status = CorporateActionReconciliationStatus.APPROVED
            reconciliation.notes = (notes or "Identity approved; transaction created idempotently.")[:5000]
        else:
            raise ValidationError("Decision must be APPROVE or REJECT.")
        reconciliation.reviewed_at = timezone.now()
        reconciliation.reviewed_by = owner
        reconciliation.save()
        CorporateActionNotification.objects.filter(owner=owner, action=action).update(
            reviewed=True, dismissed=(decision == "REJECT"), updated_at=timezone.now()
        )
        return reconciliation
