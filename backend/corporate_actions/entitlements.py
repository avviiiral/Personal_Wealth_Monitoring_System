"""Decimal entitlement calculations for corporate actions.

Pure functions with no database access. ``calculate_entitlement`` receives
the verified event terms and the eligible holding of one portfolio position
(split into the Asset "legs" that make it up) and returns either the ledger
rows needed to account for the event, or the reason it must be reviewed
manually. Nothing here guesses a missing term.

Ratio conventions (the existing ``CorporateAction`` fields):

* BONUS / STOCK_DIVIDEND: ``new_units`` additional shares for every
  ``old_units`` held. ``old_units=1, new_units=2`` on 100 shares gives 200
  bonus shares and a holding of 300.
* STOCK_SPLIT / REVERSE_SPLIT / ADR_RATIO_CHANGE: ``old_units`` shares become
  ``new_units`` shares. A 1 -> 2 split turns 100 into 200; a 10 -> 1 reverse
  split turns 1000 into 100.
* MERGER: ``new_units`` successor shares for every ``old_units`` original
  shares; the original holding ends. ``cash_per_unit`` is any cash paid per
  original share.
* DEMERGER: ``new_units`` resulting-company shares for every ``old_units``
  held; the original holding continues. ``cost_allocation_ratio`` is the
  official fraction of the original cost that moves to the new company.
* CASH_DIVIDEND: ``cash_per_unit`` per entitled share. Quantity is unchanged.

Entitlements are calculated in whole shares on the total position. A
fractional remainder is never dropped silently: bonus fractions are reported
as cash in lieu pending (bonus shares carry no cost, so the cost basis is
unaffected); for events that move cost (splits, reverse splits, mergers,
demergers) a fraction needs the official cash-in-lieu price, otherwise the
position goes to manual review.
"""
from dataclasses import dataclass, field
from decimal import ROUND_FLOOR, ROUND_HALF_UP, Decimal

ZERO = Decimal("0")
ONE = Decimal("1")
QUANTITY = Decimal("0.000001")   # Transaction.quantity / price_per_unit precision
MONEY = Decimal("0.0001")        # Transaction.amount precision

STATUS_OK = "OK"
STATUS_MANUAL = "MANUAL_REVIEW"
STATUS_NOT_ELIGIBLE = "NOT_ELIGIBLE"

ROLE_SOURCE = "SOURCE"   # the security the event was announced for
ROLE_RESULT = "RESULT"   # successor / resulting security (merger, demerger)

BONUS_TYPES = {"BONUS", "STOCK_DIVIDEND"}
SPLIT_TYPES = {"STOCK_SPLIT", "REVERSE_SPLIT", "ADR_RATIO_CHANGE"}

MANUAL_ONLY_REASONS = {
    "RIGHTS_ISSUE": "A rights entitlement depends on your subscription decision; record any allotment manually.",
    "BUYBACK": "Shares accepted in a buyback depend on your tender and the acceptance ratio; record the sale manually.",
    "TENDER_OFFER": "Shares accepted in a tender offer depend on your tender and the acceptance ratio; record the sale manually.",
    "CASH_IN_LIEU": "Cash-in-lieu proceeds must be recorded from the actual payment received.",
    "WARRANT_CONVERSION": "Warrant conversion requires the conversion price and your exercise; record it manually.",
    "TICKER_CHANGE": "A ticker change has no accounting effect; no ledger transaction is required.",
    "ISIN_CHANGE": "An ISIN change updates the security identity, not the ledger; update the asset's ISIN instead.",
    "DELISTING": "Delisting or suspension needs the exit price or offer terms; record it manually.",
    "MF_DISTRIBUTION": "Mutual-fund distributions are recorded in the mutual-fund ledger, not as direct stock transactions.",
    "MF_OPTION_CHANGE": "Mutual-fund plan/option changes are recorded in the mutual-fund ledger.",
    "MF_UNIT_ADJUSTMENT": "Mutual-fund unit adjustments are recorded in the mutual-fund ledger.",
}


@dataclass(frozen=True)
class Terms:
    action_type: str
    old_units: Decimal | None = None
    new_units: Decimal | None = None
    cash_per_unit: Decimal | None = None
    cost_allocation_ratio: Decimal | None = None
    cash_in_lieu_price: Decimal | None = None
    resulting_isin: str = ""
    resulting_symbol: str = ""
    resulting_security_name: str = ""

    @classmethod
    def from_action(cls, action):
        return cls(
            action_type=str(action.action_type or ""),
            old_units=action.old_units,
            new_units=action.new_units,
            cash_per_unit=action.cash_per_unit,
            cost_allocation_ratio=getattr(action, "cost_allocation_ratio", None),
            cash_in_lieu_price=getattr(action, "cash_in_lieu_price", None),
            resulting_isin=(action.resulting_isin or "").strip().upper(),
            resulting_symbol=(action.resulting_symbol or "").strip().upper(),
            resulting_security_name=(getattr(action, "resulting_security_name", "") or "").strip(),
        )

    @property
    def ratio_text(self):
        if self.old_units is None or self.new_units is None:
            return ""
        return f"{_format_decimal(self.old_units)}:{_format_decimal(self.new_units)}"


@dataclass(frozen=True)
class LegInput:
    key: str
    quantity: Decimal
    cost: Decimal


@dataclass
class PlannedEntry:
    leg_key: str
    role: str
    transaction_type: str
    quantity: Decimal
    price_per_unit: Decimal
    amount: Decimal
    date_field: str  # "ex_date" or "payment_date"
    label: str

    def as_dict(self):
        return {
            "leg_key": self.leg_key, "role": self.role,
            "transaction_type": self.transaction_type,
            "quantity": str(self.quantity), "price_per_unit": str(self.price_per_unit),
            "amount": str(self.amount), "date_field": self.date_field, "label": self.label,
        }


@dataclass
class EntitlementResult:
    status: str
    reason: str = ""
    eligible_quantity: Decimal = ZERO
    cost_basis: Decimal = ZERO
    quantity_delta: Decimal | None = None
    resulting_quantity: Decimal | None = None
    new_security_quantity: Decimal | None = None
    fractional_entitlement: Decimal = ZERO
    cash_in_lieu_pending: bool = False
    cash_amount: Decimal | None = None
    resulting_cost_basis: Decimal | None = None
    allocated_cost: Decimal | None = None
    entries: list = field(default_factory=list)
    explanation: str = ""

    @property
    def ok(self):
        return self.status == STATUS_OK


def _format_decimal(value):
    if value is None:
        return ""
    normalized = Decimal(value).normalize()
    text = format(normalized, "f")
    return text


def _money(value):
    return Decimal(value).quantize(MONEY, rounding=ROUND_HALF_UP)


def _qty(value):
    return Decimal(value).quantize(QUANTITY, rounding=ROUND_HALF_UP)


def _floor(value):
    return Decimal(value).to_integral_value(rounding=ROUND_FLOOR)


def _price(amount, quantity):
    if not quantity:
        return ZERO
    return (Decimal(amount) / Decimal(quantity)).quantize(QUANTITY, rounding=ROUND_HALF_UP)


def _manual(reason, legs, **extra):
    return EntitlementResult(
        status=STATUS_MANUAL, reason=reason,
        eligible_quantity=sum((leg.quantity for leg in legs), ZERO),
        cost_basis=sum((leg.cost for leg in legs), ZERO), **extra,
    )


def allocate_whole_shares(exact_by_leg, total_whole):
    """Allocate ``total_whole`` shares across legs by largest remainder.

    Each leg first receives the floor of its exact entitlement; any shares
    left over (because the position total is rounded once, not per leg) go to
    the legs with the largest fractional parts, ties to the earlier leg.
    """
    floors = {key: _floor(value) for key, value in exact_by_leg.items()}
    remaining = total_whole - sum(floors.values(), ZERO)
    order = sorted(
        exact_by_leg,
        key=lambda key: (-(exact_by_leg[key] - floors[key]), list(exact_by_leg).index(key)),
    )
    allocation = dict(floors)
    index = 0
    while remaining > ZERO and order:
        allocation[order[index % len(order)]] += ONE
        remaining -= ONE
        index += 1
    return allocation


def _require_ratio(terms, legs):
    if terms.old_units is None or terms.new_units is None:
        return _manual("The official entitlement ratio (old units : new units) is not available.", legs)
    if terms.old_units <= ZERO or terms.new_units <= ZERO:
        return _manual("The entitlement ratio must be greater than zero.", legs)
    return None


def calculate_entitlement(terms, legs):
    """Calculate the accounting entries for one position. See module docstring."""
    legs = [leg for leg in legs if leg.quantity is not None]
    eligible_legs = [leg for leg in legs if leg.quantity > ZERO]
    total_quantity = sum((leg.quantity for leg in eligible_legs), ZERO)
    total_cost = sum((leg.cost for leg in eligible_legs), ZERO)
    action_type = terms.action_type

    if action_type in MANUAL_ONLY_REASONS:
        return _manual(MANUAL_ONLY_REASONS[action_type], eligible_legs or legs)

    if total_quantity <= ZERO:
        return EntitlementResult(
            status=STATUS_NOT_ELIGIBLE,
            reason="No positive holding before the ex-date; this position has no entitlement.",
            eligible_quantity=ZERO, cost_basis=ZERO,
        )

    if action_type in BONUS_TYPES:
        return _bonus(terms, eligible_legs, total_quantity, total_cost)
    if action_type in SPLIT_TYPES:
        return _split(terms, eligible_legs, total_quantity, total_cost)
    if action_type == "CASH_DIVIDEND":
        return _dividend(terms, eligible_legs, total_quantity, total_cost)
    if action_type == "MERGER":
        return _merger(terms, eligible_legs, total_quantity, total_cost)
    if action_type == "DEMERGER":
        return _demerger(terms, eligible_legs, total_quantity, total_cost)
    return _manual(f"Corporate-action type {action_type or 'unknown'} has no automatic accounting treatment.", eligible_legs)


def _bonus(terms, legs, total_quantity, total_cost):
    problem = _require_ratio(terms, legs)
    if problem:
        return problem
    factor = terms.new_units / terms.old_units
    exact_by_leg = {leg.key: leg.quantity * factor for leg in legs}
    exact_total = total_quantity * factor
    whole_total = _floor(exact_total)
    fraction = _qty(exact_total - whole_total)
    if whole_total <= ZERO:
        return _manual(
            f"The entitlement is only fractional ({_format_decimal(fraction)} share) and is settled by the company in cash; "
            "record the cash-in-lieu proceeds when received.",
            legs, fractional_entitlement=fraction, cash_in_lieu_pending=fraction > ZERO,
        )
    allocation = allocate_whole_shares(exact_by_leg, whole_total)
    entries = [
        PlannedEntry(leg.key, ROLE_SOURCE, "BONUS", allocation[leg.key], ZERO, ZERO, "ex_date", "Bonus shares")
        for leg in legs if allocation[leg.key] > ZERO
    ]
    cash_amount = None
    if fraction > ZERO and terms.cash_in_lieu_price is not None:
        cash_amount = _money(fraction * terms.cash_in_lieu_price)
    explanation = (
        f"{_format_decimal(total_quantity)} eligible shares × {_format_decimal(terms.new_units)}/{_format_decimal(terms.old_units)} "
        f"= {_format_decimal(_qty(exact_total))} bonus shares; {_format_decimal(whole_total)} whole shares credited at zero cost. "
        f"Holding {_format_decimal(total_quantity)} → {_format_decimal(total_quantity + whole_total)}. Invested cost unchanged at {_format_decimal(_money(total_cost))}."
    )
    if fraction > ZERO:
        explanation += (
            f" Fractional entitlement of {_format_decimal(fraction)} share is settled in cash by the company (cash in lieu); "
            "the proceeds are not booked here — record them when received."
        )
    return EntitlementResult(
        status=STATUS_OK, eligible_quantity=total_quantity, cost_basis=total_cost,
        quantity_delta=whole_total, resulting_quantity=total_quantity + whole_total,
        fractional_entitlement=fraction, cash_in_lieu_pending=fraction > ZERO,
        cash_amount=cash_amount, resulting_cost_basis=total_cost, entries=entries,
        explanation=explanation,
    )


def _split(terms, legs, total_quantity, total_cost):
    problem = _require_ratio(terms, legs)
    if problem:
        return problem
    if terms.action_type == "STOCK_SPLIT" and terms.new_units <= terms.old_units:
        return _manual("A stock split must increase the number of shares; the stored ratio does not.", legs)
    if terms.action_type == "REVERSE_SPLIT" and terms.new_units >= terms.old_units:
        return _manual("A reverse split must reduce the number of shares; the stored ratio does not.", legs)
    if terms.new_units == terms.old_units:
        return _manual("The ratio leaves the holding unchanged; nothing to record.", legs)
    factor = terms.new_units / terms.old_units
    exact_total = total_quantity * factor
    whole_total = _floor(exact_total)
    fraction = _qty(exact_total - whole_total)
    entries = []
    sale = ZERO
    if fraction > ZERO:
        if terms.cash_in_lieu_price is None:
            return _manual(
                f"The ratio leaves a fractional entitlement of {_format_decimal(fraction)} share. Cash in lieu changes the "
                "cost basis, so the official cash-in-lieu price is required before this can be booked.",
                legs, fractional_entitlement=fraction,
            )
        if len(legs) != 1:
            return _manual(
                "A fractional entitlement across several Asset records of one position must be allocated manually.",
                legs, fractional_entitlement=fraction,
            )
        leg = legs[0]
        new_quantity = _qty(exact_total)
        entries.append(PlannedEntry(leg.key, ROLE_SOURCE, "SPLIT", new_quantity - leg.quantity, ZERO, ZERO,
                                    "ex_date", "Split adjustment" if factor > ONE else "Reverse split adjustment"))
        sale = _money(fraction * terms.cash_in_lieu_price)
        entries.append(PlannedEntry(leg.key, ROLE_SOURCE, "SELL", fraction, _price(sale, fraction), sale,
                                    "payment_date", "Cash in lieu of fractional entitlement"))
    else:
        exact_by_leg = {leg.key: leg.quantity * factor for leg in legs}
        allocation = allocate_whole_shares(exact_by_leg, whole_total)
        for leg in legs:
            delta = allocation[leg.key] - leg.quantity
            if delta != ZERO:
                entries.append(PlannedEntry(leg.key, ROLE_SOURCE, "SPLIT", delta, ZERO, ZERO, "ex_date",
                                            "Split adjustment" if factor > ONE else "Reverse split adjustment"))
    if not entries:
        return _manual("The ratio leaves the holding unchanged; nothing to record.", legs)
    resulting = whole_total
    verb = "split" if factor > ONE else "reverse split"
    explanation = (
        f"{_format_decimal(total_quantity)} shares × {_format_decimal(terms.new_units)}/{_format_decimal(terms.old_units)} ({verb}) "
        f"= {_format_decimal(_qty(exact_total))}. Holding {_format_decimal(total_quantity)} → {_format_decimal(resulting)} "
        f"(change {_format_decimal(resulting - total_quantity)}). Invested cost unchanged at {_format_decimal(_money(total_cost))}; "
        f"average cost {_format_decimal(_qty(total_cost / total_quantity))} → "
        f"{_format_decimal(_qty(total_cost / resulting)) if resulting > ZERO else '—'}."
    )
    if fraction > ZERO:
        explanation += (
            f" Fractional {_format_decimal(fraction)} share sold for cash in lieu at {_format_decimal(terms.cash_in_lieu_price)} "
            f"= {_format_decimal(sale)}; its share of the cost is released by the sale."
        )
    return EntitlementResult(
        status=STATUS_OK, eligible_quantity=total_quantity, cost_basis=total_cost,
        quantity_delta=resulting - total_quantity, resulting_quantity=resulting,
        fractional_entitlement=fraction, cash_amount=sale if fraction > ZERO else None,
        resulting_cost_basis=total_cost, entries=entries, explanation=explanation,
    )


def _dividend(terms, legs, total_quantity, total_cost):
    if terms.cash_per_unit is None or terms.cash_per_unit <= ZERO:
        return _manual("The verified dividend per share is not available.", legs)
    dps = terms.cash_per_unit
    entries = []
    total_amount = ZERO
    for leg in legs:
        amount = _money(leg.quantity * dps)
        total_amount += amount
        entries.append(PlannedEntry(leg.key, ROLE_SOURCE, "DIVIDEND", leg.quantity, _qty(dps), amount,
                                    "payment_date", "Cash dividend"))
    explanation = (
        f"{_format_decimal(total_quantity)} entitled shares × {_format_decimal(dps)} per share = {_format_decimal(total_amount)} gross dividend "
        "(before any tax deducted at source). Share quantity and invested cost are unchanged."
    )
    return EntitlementResult(
        status=STATUS_OK, eligible_quantity=total_quantity, cost_basis=total_cost,
        quantity_delta=ZERO, resulting_quantity=total_quantity, cash_amount=total_amount,
        resulting_cost_basis=total_cost, entries=entries, explanation=explanation,
    )


def _resulting_label(terms):
    return terms.resulting_security_name or terms.resulting_symbol or terms.resulting_isin


def _resulting_shares(terms, legs, total_quantity, label):
    """Whole resulting shares per leg, or a manual-review result."""
    factor = terms.new_units / terms.old_units
    exact_total = total_quantity * factor
    whole_total = _floor(exact_total)
    fraction = _qty(exact_total - whole_total)
    if fraction > ZERO:
        if terms.cash_in_lieu_price is None:
            return None, _manual(
                f"The ratio leaves a fractional entitlement of {_format_decimal(fraction)} {label} share. Cash in lieu changes "
                "the cost basis, so the official cash-in-lieu price is required before this can be booked.",
                legs, fractional_entitlement=fraction,
            )
        if len(legs) != 1:
            return None, _manual(
                "A fractional entitlement across several Asset records of one position must be allocated manually.",
                legs, fractional_entitlement=fraction,
            )
        return {legs[0].key: _qty(exact_total)}, (exact_total, whole_total, fraction)
    exact_by_leg = {leg.key: leg.quantity * factor for leg in legs}
    return allocate_whole_shares(exact_by_leg, whole_total), (exact_total, whole_total, fraction)


def _merger(terms, legs, total_quantity, total_cost):
    problem = _require_ratio(terms, legs)
    if problem:
        return problem
    if not terms.resulting_isin:
        return _manual("The successor security ISIN is required to record a merger.", legs)
    cash = terms.cash_per_unit or ZERO
    if cash < ZERO:
        return _manual("The cash component cannot be negative.", legs)
    ratio = terms.cost_allocation_ratio
    if cash > ZERO:
        if ratio is None:
            return _manual(
                "The merger pays cash as well as shares; the official cost allocation between the successor "
                "shares and the cash is required.", legs,
            )
        if ratio <= ZERO or ratio > ONE:
            return _manual("The cost allocation ratio must be greater than 0 and at most 1.", legs)
    else:
        if ratio is not None and ratio != ONE:
            return _manual(
                "A share-for-share merger carries the whole cost basis to the successor shares; the stored "
                "cost allocation ratio is inconsistent with that.", legs,
            )
        ratio = ONE
    label = _resulting_label(terms)
    allocation, info = _resulting_shares(terms, legs, total_quantity, label)
    if allocation is None:
        return info
    exact_total, whole_total, fraction = info
    if whole_total <= ZERO and fraction <= ZERO:
        return _manual("The ratio gives no successor shares.", legs)
    entries = []
    carried_total = ZERO
    cash_total = ZERO
    for leg in legs:
        carried = _money(leg.cost * ratio)
        leg_cash = _money(leg.quantity * cash)
        carried_total += carried
        cash_total += leg_cash
        proceeds = carried + leg_cash
        entries.append(PlannedEntry(leg.key, ROLE_SOURCE, "SELL", leg.quantity, _price(proceeds, leg.quantity),
                                    proceeds, "ex_date", f"Merger: shares converted into {label}"))
        new_quantity = allocation[leg.key]
        entries.append(PlannedEntry(leg.key, ROLE_RESULT, "BUY", new_quantity, _price(carried, new_quantity),
                                    carried, "ex_date", "Merger: cost carried from original shares"))
    sale = ZERO
    if fraction > ZERO:
        leg = legs[0]
        sale = _money(fraction * terms.cash_in_lieu_price)
        entries.append(PlannedEntry(leg.key, ROLE_RESULT, "SELL", fraction, _price(sale, fraction), sale,
                                    "payment_date", "Cash in lieu of fractional successor share"))
    explanation = (
        f"{_format_decimal(total_quantity)} original shares × {_format_decimal(terms.new_units)}/{_format_decimal(terms.old_units)} "
        f"= {_format_decimal(_qty(exact_total))} {label} shares. The original holding is closed at its cost; "
        f"{_format_decimal(carried_total)} of the {_format_decimal(_money(total_cost))} cost basis is carried to {_format_decimal(whole_total)} successor shares"
    )
    explanation += (f" and {_format_decimal(cash_total)} cash is received ({_format_decimal(cash)} per share)." if cash > ZERO else ".")
    if fraction > ZERO:
        explanation += f" Fractional {_format_decimal(fraction)} successor share sold for cash in lieu = {_format_decimal(sale)}."
    return EntitlementResult(
        status=STATUS_OK, eligible_quantity=total_quantity, cost_basis=total_cost,
        quantity_delta=-total_quantity, resulting_quantity=ZERO, new_security_quantity=whole_total,
        fractional_entitlement=fraction, cash_amount=(cash_total + sale) if (cash > ZERO or sale) else None,
        resulting_cost_basis=ZERO, allocated_cost=carried_total, entries=entries, explanation=explanation,
    )


def _demerger(terms, legs, total_quantity, total_cost):
    problem = _require_ratio(terms, legs)
    if problem:
        return problem
    if not terms.resulting_isin:
        return _manual("The resulting company ISIN is required to record a demerger.", legs)
    if terms.cash_per_unit:
        return _manual("A demerger with a cash component must be recorded manually.", legs)
    ratio = terms.cost_allocation_ratio
    if ratio is None:
        return _manual(
            "The official cost allocation between the original and the resulting company is required "
            "(from the company's cost-of-acquisition notice).", legs,
        )
    if ratio <= ZERO or ratio >= ONE:
        return _manual("The cost allocation ratio for a demerger must be between 0 and 1.", legs)
    label = _resulting_label(terms)
    allocation, info = _resulting_shares(terms, legs, total_quantity, label)
    if allocation is None:
        return info
    exact_total, whole_total, fraction = info
    entries = []
    moved_total = ZERO
    for leg in legs:
        cost = _money(leg.cost)
        moved = _money(cost * ratio)
        moved_total += moved
        retained = cost - moved
        entries.append(PlannedEntry(leg.key, ROLE_SOURCE, "SELL", leg.quantity, _price(cost, leg.quantity), cost,
                                    "ex_date", "Demerger: original cost removed"))
        entries.append(PlannedEntry(leg.key, ROLE_SOURCE, "BUY", leg.quantity, _price(retained, leg.quantity), retained,
                                    "ex_date", "Demerger: cost retained after the demerger"))
        new_quantity = allocation[leg.key]
        if new_quantity > ZERO:
            entries.append(PlannedEntry(leg.key, ROLE_RESULT, "BUY", new_quantity, _price(moved, new_quantity), moved,
                                        "ex_date", "Demerger: cost allocated to resulting company"))
    sale = ZERO
    if fraction > ZERO:
        leg = legs[0]
        sale = _money(fraction * terms.cash_in_lieu_price)
        entries.append(PlannedEntry(leg.key, ROLE_RESULT, "SELL", fraction, _price(sale, fraction), sale,
                                    "payment_date", "Cash in lieu of fractional resulting share"))
    explanation = (
        f"{_format_decimal(total_quantity)} shares are retained and {_format_decimal(total_quantity)} × {_format_decimal(terms.new_units)}/"
        f"{_format_decimal(terms.old_units)} = {_format_decimal(_qty(exact_total))} {label} shares are received. Of the "
        f"{_format_decimal(_money(total_cost))} cost basis, {_format_decimal(ratio * 100)}% ({_format_decimal(moved_total)}) moves to {label} and "
        f"{_format_decimal(_money(total_cost) - moved_total)} stays with the original shares; the total cost is not duplicated."
    )
    if fraction > ZERO:
        explanation += f" Fractional {_format_decimal(fraction)} share sold for cash in lieu = {_format_decimal(sale)}."
    return EntitlementResult(
        status=STATUS_OK, eligible_quantity=total_quantity, cost_basis=total_cost,
        quantity_delta=ZERO, resulting_quantity=total_quantity, new_security_quantity=whole_total,
        fractional_entitlement=fraction, cash_amount=sale if fraction > ZERO else None,
        resulting_cost_basis=_money(total_cost) - moved_total, allocated_cost=moved_total,
        entries=entries, explanation=explanation,
    )
