"""Apply confirmed corporate-action quantity ratios to ordered transaction streams.

This module deliberately changes quantity only. A split/bonus must not change
the acquisition cost basis, and callers remain responsible for choosing the
correct family/owner scope and compatible market-price convention.
"""
from decimal import Decimal

from investments.models import TransactionType
from .models import CorporateAction, CorporateActionStatus, RATIO_ACTIONS


ZERO = Decimal("0")
BUY_TYPES = (TransactionType.BUY, TransactionType.SIP)
NON_CASH_QUANTITY_ACTIONS = (TransactionType.BONUS, TransactionType.SPLIT)


def confirmed_ratio_actions(*, asset, owner, as_of_date=None):
    """Return confirmed ratio actions for this exact security, in event-date order."""
    if owner is None or asset is None:
        return []
    query = CorporateAction.objects.filter(
        owner=owner,
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


def calculate_position_with_actions(transactions, actions):
    """Replay transactions and confirmed split/bonus ratios as one dated event stream.

    Split/bonus events multiply the quantity held immediately before their ex-date.
    They do not alter invested value. An explicit BONUS/SPLIT transaction on the
    same date takes precedence to avoid applying the same event twice.
    """
    txs = list(transactions)
    action_rows = list(actions)
    # Suppress a feed action only when an explicit quantity transaction exists
    # for the same event date and mapped transaction kind. Keep the suppression
    # local to this exact transaction stream so separately held assets don't
    # share deduplication state.
    explicit_action_dates = {
        (tx.transaction_date, tx.transaction_type)
        for tx in txs
        if tx.transaction_type in NON_CASH_QUANTITY_ACTIONS
    }
    events = []
    for tx in txs:
        events.append((tx.transaction_date, 1, tx.created_at, tx.id, "transaction", tx))
    for action in action_rows:
        explicit_type = (
            TransactionType.BONUS
            if action.action_type in {"BONUS", "STOCK_DIVIDEND"}
            else TransactionType.SPLIT
        )
        if (action.ex_date, explicit_type) in explicit_action_dates:
            continue
        if not action.old_units or not action.new_units or action.old_units <= ZERO:
            continue
        events.append((action.ex_date, 0, action.created_at, action.id, "action", action))
    events.sort(key=lambda event: (event[0], event[1], event[2], event[3]))

    quantity = ZERO
    invested_value = ZERO
    for _, _, _, _, event_type, item in events:
        if event_type == "action":
            quantity *= item.new_units / item.old_units
            continue

        tx = item
        tx_quantity = tx.quantity or ZERO
        tx_amount = tx.amount or ZERO
        if tx.transaction_type in BUY_TYPES:
            if tx_quantity > ZERO:
                quantity += tx_quantity
            if tx_amount > ZERO:
                invested_value += tx_amount
        elif tx.transaction_type == TransactionType.SELL:
            if tx_quantity <= ZERO or quantity <= ZERO:
                continue
            average_cost = invested_value / quantity if quantity > ZERO else ZERO
            sold = min(tx_quantity, quantity)
            quantity -= sold
            invested_value -= average_cost * sold
            if quantity <= ZERO:
                quantity = ZERO
                invested_value = ZERO
        elif tx.transaction_type in NON_CASH_QUANTITY_ACTIONS:
            if tx_quantity > ZERO:
                quantity += tx_quantity

    average_cost = invested_value / quantity if quantity > ZERO else ZERO
    return {
        "quantity": quantity,
        "invested_value": invested_value,
        "average_cost": average_cost,
    }
