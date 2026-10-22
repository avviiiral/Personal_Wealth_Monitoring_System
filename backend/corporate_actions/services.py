from datetime import date
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
