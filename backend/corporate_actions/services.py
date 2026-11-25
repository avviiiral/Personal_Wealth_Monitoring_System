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

CORPORATE_ACTION_DUPLICATE_FIELDS = (
    "underlying",
    "asset_name",
    "sub_class",
    "asset_class",
    "family_name",
)


def _normalized_hierarchy_value(value):
    return str(value or "").strip().casefold()


def _existing_corporate_action_transaction(
    *,
    owner,
    family_id,
    transaction_date,
    transaction_type,
    hierarchy_values,
    lock=False,
):
    """Find an existing same-day action row for the same investor hierarchy.

    Portfolio and advisor labels are intentionally excluded: the duplicate
    identity is the underlying, asset name, subclass, asset class and family
    member, within the family (or individual account) and transaction date.
    """
    query = Transaction.objects.select_related("asset").filter(
        transaction_date=transaction_date,
        transaction_type=transaction_type,
    )
    if lock:
        query = query.select_for_update()
    if family_id is not None:
        query = query.filter(family_id=family_id)
    else:
        query = query.filter(owner=owner, family__isnull=True)

    target = {
        field: _normalized_hierarchy_value(hierarchy_values.get(field))
        for field in CORPORATE_ACTION_DUPLICATE_FIELDS
    }
    for candidate in query.order_by("id"):
        candidate_asset_name = candidate.asset_name or candidate.asset.name
        candidate_values = {
            "underlying": candidate.underlying or candidate_asset_name,
            "asset_name": candidate_asset_name,
            "sub_class": candidate.sub_class,
            "asset_class": candidate.asset_class,
            "family_name": candidate.family_name,
        }
        if all(
            _normalized_hierarchy_value(candidate_values[field]) == target[field]
            for field in CORPORATE_ACTION_DUPLICATE_FIELDS
        ):
            return candidate
    return None


def _create_reconciliation(action, user, status_value, transaction_ids=None, suggested_quantity=None, notes="",
                           identity_match_method=""):
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
            "identity_match_method": identity_match_method,
        },
    )
    if identity_match_method and not reconciliation.identity_match_method:
        reconciliation.identity_match_method = identity_match_method
        reconciliation.save(update_fields=["identity_match_method", "updated_at"])
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
        from corporate_actions.identity import symbol_q

        symbol_matches = list(assets.filter(symbol_q(action.symbol)).order_by("name", "id"))
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
            from corporate_actions.identity import symbol_q

            identity_query |= symbol_q(action.symbol)
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
    # Set by the NSE scan when nse_identity_matching is on (SOURCE_ISIN,
    # SYMBOL_SECURITY_MASTER, SYMBOL_EQUITY_L); empty otherwise.
    isin_match_method = str((action.metadata or {}).get("identity_match_method") or "")[:24]
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

            existing_transactions = []
            if expected_type is not None:
                for prior in tx_rows:
                    hierarchy_values = {
                        "underlying": prior.underlying or prior.asset_name or prior.asset.name,
                        "asset_name": prior.asset_name or prior.asset.name,
                        "sub_class": prior.sub_class,
                        "asset_class": prior.asset_class,
                        "family_name": prior.family_name,
                    }
                    match = _existing_corporate_action_transaction(
                        owner=user,
                        family_id=family_id,
                        transaction_date=action.ex_date,
                        transaction_type=expected_type,
                        hierarchy_values=hierarchy_values,
                    )
                    if match is not None and match.id not in {
                        item.id for item in existing_transactions
                    }:
                        existing_transactions.append(match)
            existing = existing_transactions

            if existing:
                _create_reconciliation(
                    action, user, CorporateActionReconciliationStatus.ALREADY_RECORDED,
                    [item.id for item in existing[:50]],
                    identity_match_method=isin_match_method,
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
                        identity_match_method=isin_match_method,
                    )
                    notification_count += 1
                else:
                    _create_reconciliation(
                        action, user, CorporateActionReconciliationStatus.MANUAL_REVIEW,
                        suggested_quantity=eligible_quantity,
                        notes="No positive eligible direct holding was established immediately before the ex-date. No automatic position adjustment will be applied for this investor.",
                        identity_match_method=isin_match_method,
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
    """Approve/reject a proposed identity without duplicating existing transaction rows."""
    from collections import defaultdict
    from investments.models import Transaction, TransactionSource, TransactionType
    from corporate_actions.position_adjustment import (
        calculate_position_with_actions,
        confirmed_ratio_actions,
    )

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
            reconciliation.notes = (
                notes or "User rejected the proposed identity. No transaction was changed."
            )[:5000]
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
            asset = Asset.objects.select_for_update().filter(
                id=asset_id, is_active=True, category="STOCK"
            ).filter(identity_scope).first()
            if asset is None:
                raise ValidationError("Selected holding is no longer available.")
            family_id = asset.family_id
            reconciliation.family_id_snapshot = family_id

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

            is_bonus = transaction_type == TransactionType.BONUS
            if is_bonus and not action.record_date:
                raise ValidationError("Official record date is required to allocate bonus shares.")
            if transaction_type != TransactionType.DIVIDEND:
                if not action.old_units or not action.new_units:
                    raise ValidationError("A verified entitlement ratio is required.")

            # Preserve portfolio hierarchy and allocate the event separately for
            # every eligible bucket. The duplicate identity deliberately omits
            # portfolio/advisor, matching the requested five-field scope.
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

            eligibility_date = action.record_date if is_bonus else action.ex_date
            prior_rows = Transaction.objects.filter(
                asset_id__in=matching_asset_ids,
                transaction_date__lte=eligibility_date,
            )
            if not is_bonus:
                prior_rows = prior_rows.filter(transaction_date__lt=action.ex_date)
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

            matched_transactions = []
            created_count = 0
            skipped_count = 0
            for bucket, bucket_transactions in grouped_rows.items():
                # Reconstruct each Asset-specific position, then aggregate only
                # within the same complete hierarchy path.
                rows_by_asset = defaultdict(list)
                for prior in bucket_transactions:
                    rows_by_asset[prior.asset_id].append(prior)

                bucket_quantity = Decimal("0")
                for prior_asset_id, asset_rows in rows_by_asset.items():
                    prior_asset = asset_rows[0].asset
                    earlier_actions = [
                        item for item in confirmed_ratio_actions(asset=prior_asset, owner=owner)
                        if (
                            item.ex_date <= eligibility_date
                            if is_bonus
                            else item.ex_date < action.ex_date
                        )
                    ]
                    position = calculate_position_with_actions(asset_rows, earlier_actions)
                    bucket_quantity += position.get("quantity") or Decimal("0")

                if bucket_quantity <= 0:
                    continue

                if is_bonus:
                    eligible_lots = (bucket_quantity / action.old_units).to_integral_value(
                        rounding=ROUND_FLOOR
                    )
                    transaction_quantity = eligible_lots * action.new_units
                    price = Decimal("0")
                    amount = Decimal("0")
                elif transaction_type == TransactionType.DIVIDEND:
                    transaction_quantity = bucket_quantity
                    price = action.cash_per_unit or Decimal("0")
                    amount = transaction_quantity * price
                else:
                    transaction_quantity = bucket_quantity * (
                        action.new_units / action.old_units - Decimal("1")
                    )
                    price = Decimal("0")
                    amount = Decimal("0")

                if transaction_quantity <= 0:
                    continue

                bucket_identity = "|".join(str(value) for value in bucket)
                representative_asset = bucket_transactions[0].asset
                defaults = {
                    "owner": owner,
                    "family": representative_asset.family,
                    "asset": representative_asset,
                    "transaction_type": transaction_type,
                    "transaction_date": action.ex_date,
                    "quantity": transaction_quantity,
                    "price_per_unit": price,
                    "amount": amount,
                    "fees": Decimal("0"),
                    "notes": (
                        f"User-approved corporate action #{action.id}; "
                        f"identity matched by {reconciliation.identity_match_method}."
                    ),
                }
                defaults.update(dict(zip(hierarchy_fields, bucket)))
                duplicate_hierarchy = {
                    field: defaults.get(field)
                    for field in CORPORATE_ACTION_DUPLICATE_FIELDS
                }
                existing = _existing_corporate_action_transaction(
                    owner=owner,
                    family_id=representative_asset.family_id,
                    transaction_date=action.ex_date,
                    transaction_type=transaction_type,
                    hierarchy_values=duplicate_hierarchy,
                    lock=True,
                )
                if existing is not None:
                    if existing.id not in {item.id for item in matched_transactions}:
                        matched_transactions.append(existing)
                    skipped_count += 1
                    continue

                key = hashlib.sha256(
                    (
                        f"corporate-action:{action.id}:owner:{owner.id}:"
                        f"family:{representative_asset.family_id}:bucket:{bucket_identity}"
                    ).encode()
                ).hexdigest()
                transaction, was_created = Transaction.objects.get_or_create(
                    family=representative_asset.family,
                    source=TransactionSource.OTHER,
                    source_key=key,
                    defaults=defaults,
                )
                if transaction.id not in {item.id for item in matched_transactions}:
                    matched_transactions.append(transaction)
                if was_created:
                    created_count += 1
                else:
                    skipped_count += 1

            if not matched_transactions:
                if is_bonus:
                    raise ValidationError(
                        "No positive whole-share bonus entitlement was found across the record-date portfolio hierarchy."
                    )
                raise ValidationError(
                    "No positive eligible entitlement was found across the portfolio hierarchy."
                )

            reconciliation.selected_asset = asset
            reconciliation.matched_transaction_ids = [item.id for item in matched_transactions]
            reconciliation.status = CorporateActionReconciliationStatus.APPROVED
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
