"""Ledger replay shared by every position calculator.

The transaction ledger is the only accounting source of truth. A corporate
action changes a holding only after the user accepts it and the matching
BONUS / SPLIT / DIVIDEND / SELL / BUY rows exist in ``Transaction``. Persisted
``CorporateAction`` rows (whatever their verification status) are never
overlaid on positions by the calculation engines.

Conventions applied by ``replay_ledger``:

* BUY / SIP add quantity and acquisition cost.
* SELL removes quantity at the running average cost.
* BONUS adds quantity at zero cost.
* SPLIT carries the *change* in quantity: positive for a split, negative for
  a reverse split. Invested value is unchanged; the holding never goes below
  zero.
* DIVIDEND / INTEREST / DEPOSIT / WITHDRAWAL / OTHER do not change quantity.
* On the same date, BONUS and SPLIT rows are applied before other rows. A
  corporate action takes effect at the start of its ex-date, so a SELL or BUY
  dated on the ex-date is a post-event trade.
"""
from datetime import datetime
from decimal import Decimal

from django.db.models import Case, IntegerField, Value, When

from investments.models import TransactionType
from users.models import FamilyMembership
from .models import CorporateAction, CorporateActionStatus, CorporateActionType, RATIO_ACTIONS


ZERO = Decimal("0")
BUY_TYPES = (TransactionType.BUY, TransactionType.SIP)
NON_CASH_QUANTITY_ACTIONS = (TransactionType.BONUS, TransactionType.SPLIT)
_MIN_DATETIME = datetime.min


def same_day_priority(transaction_type):
    """0 for corporate-action quantity rows, 1 for everything else."""
    return 0 if str(transaction_type or "").strip().upper() in NON_CASH_QUANTITY_ACTIONS else 1


def ledger_sort_key(tx):
    """Deterministic ledger order: date, adjustment-first, creation, id."""
    # Never trigger a per-row query for a deferred field; id is a stable
    # tie-breaker when the creation time is not loaded.
    get_deferred = getattr(tx, "get_deferred_fields", None)
    deferred = get_deferred() if callable(get_deferred) else ()
    created_at = None if "created_at" in deferred else getattr(tx, "created_at", None)
    if created_at is not None and getattr(created_at, "tzinfo", None) is not None:
        created_at = created_at.replace(tzinfo=None)
    return (
        tx.transaction_date,
        same_day_priority(tx.transaction_type),
        created_at or _MIN_DATETIME,
        tx.id or 0,
    )


def ledger_priority_expression():
    """SQL equivalent of ``same_day_priority`` for queryset ordering."""
    return Case(
        When(transaction_type__in=NON_CASH_QUANTITY_ACTIONS, then=Value(0)),
        default=Value(1),
        output_field=IntegerField(),
    )


def order_ledger(queryset):
    """Order a Transaction queryset exactly like ``ledger_sort_key``."""
    return queryset.annotate(
        _ledger_same_day_priority=ledger_priority_expression()
    ).order_by("transaction_date", "_ledger_same_day_priority", "created_at", "id")


def apply_ledger_row(state, tx):
    """Apply one transaction to ``state`` ({"quantity", "invested_value"}) in place."""
    quantity = state["quantity"]
    invested_value = state["invested_value"]
    tx_quantity = tx.quantity or ZERO
    tx_amount = tx.amount or ZERO
    tx_type = str(tx.transaction_type or "").strip().upper()

    if tx_type in BUY_TYPES:
        if tx_quantity > ZERO:
            quantity += tx_quantity
        if tx_amount > ZERO:
            invested_value += tx_amount
    elif tx_type == TransactionType.SELL:
        if tx_quantity > ZERO and quantity > ZERO:
            average_cost = invested_value / quantity
            sold = min(tx_quantity, quantity)
            quantity -= sold
            invested_value -= average_cost * sold
            if quantity <= ZERO:
                quantity = ZERO
                invested_value = ZERO
    elif tx_type == TransactionType.BONUS:
        if tx_quantity > ZERO:
            quantity += tx_quantity
    elif tx_type == TransactionType.SPLIT:
        # Positive: additional split shares. Negative: reverse-split reduction.
        quantity += tx_quantity
        if quantity <= ZERO:
            quantity = ZERO
            invested_value = ZERO

    state["quantity"] = quantity
    state["invested_value"] = invested_value
    return state


def replay_ledger(transactions, *, before=None):
    """Replay ``transactions`` in ledger order; optionally only rows dated before ``before``."""
    rows = [tx for tx in transactions if before is None or tx.transaction_date < before]
    rows.sort(key=ledger_sort_key)
    state = {"quantity": ZERO, "invested_value": ZERO}
    for tx in rows:
        apply_ledger_row(state, tx)
    quantity = state["quantity"]
    invested_value = state["invested_value"]
    average_cost = invested_value / quantity if quantity > ZERO else ZERO
    return {
        "quantity": quantity,
        "invested_value": invested_value,
        "average_cost": average_cost,
    }


# ----------------------------------------------------------------------------
# Informational helpers retained for existing callers and tests. They are not
# used by any holding, portfolio-position, tree, MIS or analytics calculation.
# ----------------------------------------------------------------------------

def confirmed_ratio_actions(*, asset, owner, as_of_date=None):
    """Return confirmed ratio actions for this exact security, in event-date order.

    Informational only. Verification of an event does not change holdings; the
    accepted ledger transactions do.
    """
    if owner is None or asset is None:
        return []
    owner_ids = [owner.id]
    family_id = getattr(asset, "family_id", None)
    if family_id:
        member_ids = FamilyMembership.objects.filter(
            family_group_id=family_id
        ).values_list("profile__user_id", flat=True)
        owner_ids = list(set(member_ids) | {owner.id})
    query = CorporateAction.objects.filter(
        owner_id__in=owner_ids,
        status=CorporateActionStatus.CONFIRMED,
        action_type__in=RATIO_ACTIONS,
    )
    if asset.isin:
        query = query.filter(isin__iexact=asset.isin.strip())
    elif asset.symbol:
        query = query.filter(symbol__iexact=asset.symbol.strip())
    else:
        return []
    if as_of_date is not None:
        query = query.filter(ex_date__lte=as_of_date)
    return list(query.order_by("ex_date", "id"))


def ratio_quantity_factor(action):
    """Total-holding multiplier implied by a ratio action, or None.

    BONUS / STOCK_DIVIDEND: ``new_units`` extra shares for every ``old_units``
    held, so the factor is (old + new) / old. Splits and other ratio actions:
    ``old_units`` become ``new_units``, so the factor is new / old.
    """
    if not action.old_units or not action.new_units or action.old_units <= ZERO:
        return None
    if action.action_type in {CorporateActionType.BONUS, CorporateActionType.STOCK_DIVIDEND}:
        return (action.old_units + action.new_units) / action.old_units
    return action.new_units / action.old_units


def calculate_position_with_actions(transactions, actions):
    """What-if replay of transactions plus ratio actions (not used by the engines).

    An explicit BONUS/SPLIT transaction on the same date takes precedence over
    the corresponding action so the same event is never applied twice.
    """
    txs = list(transactions)
    explicit_action_dates = {
        (tx.transaction_date, tx.transaction_type)
        for tx in txs
        if tx.transaction_type in NON_CASH_QUANTITY_ACTIONS
    }
    events = [((tx.transaction_date, 1) + ledger_sort_key(tx)[1:], "transaction", tx) for tx in txs]
    for action in actions:
        explicit_type = (
            TransactionType.BONUS
            if action.action_type in {CorporateActionType.BONUS, CorporateActionType.STOCK_DIVIDEND}
            else TransactionType.SPLIT
        )
        if (action.ex_date, explicit_type) in explicit_action_dates:
            continue
        factor = ratio_quantity_factor(action)
        if factor is None:
            continue
        created_at = action.created_at.replace(tzinfo=None) if action.created_at else _MIN_DATETIME
        events.append(((action.ex_date, 0, 0, created_at, action.id), "action", factor))
    events.sort(key=lambda event: event[0])

    state = {"quantity": ZERO, "invested_value": ZERO}
    for _, event_type, item in events:
        if event_type == "action":
            state["quantity"] *= item
            continue
        apply_ledger_row(state, item)

    quantity = state["quantity"]
    invested_value = state["invested_value"]
    average_cost = invested_value / quantity if quantity > ZERO else ZERO
    return {
        "quantity": quantity,
        "invested_value": invested_value,
        "average_cost": average_cost,
    }
