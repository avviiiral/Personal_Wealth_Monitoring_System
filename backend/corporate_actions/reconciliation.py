"""Per-position reconciliation of corporate actions against the transaction ledger.

Detection and accounting are separate operations:

* ``build_plan`` / ``refresh_reconciliation`` are read-only with respect to
  the ledger. They find every eligible ledger position of the reconciliation
  owner, detect actions that are already recorded, calculate the entitlement
  and store the proposal (``CorporateActionPosition``) for review.
* ``accept_reconciliation`` is the only code path that writes corporate-action
  rows into ``investments.Transaction``, and only after explicit user approval.

Scope. A reconciliation belongs to one user. Its positions come from the
families that user is a member of (``FamilyMembership``) plus the user's own
individual (family-less) ledger, never from the family currently selected in
the frontend.

Positions. Transactions of the security are grouped by
(family scope, family member, portfolio, asset class, subclass, asset name,
underlying) - the same hierarchy the portfolio engines use. Each group is
adjusted separately; Asset records that share the ISIN inside one group are
its "legs".

Already recorded. A position is skipped when the ledger already holds a
corporate-action row of the expected type for the same family scope, family
member, asset class, subclass, asset name and security (underlying label,
Asset or ISIN) inside the event window. The portfolio label decides between
sibling positions; when it cannot, the position goes to manual review.
Existing rows are never modified. For a buyback or tender offer the evidence
is a SELL of the security at the buyback price (within 1%) in the event
window; a buyback the user did not tender in is simply rejected.

Buybacks. The number of shares a company accepts is not published, so a
buyback proposal shows the eligible holding and asks for the accepted quantity
when the user accepts; nothing is planned until then.
"""
import hashlib
import json
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import transaction as db_transaction
from django.db.models import Q
from django.utils import timezone

from config.pwms_config import get as get_pwms_config
from investments.models import Asset, PendingRestructuring, Transaction, TransactionSource, TransactionType
from users.models import FamilyGroup, FamilyMembership

from .announcement_terms import buyback_method, fill_terms_from_announcements
from .entitlements import (
    ROLE_RESULT,
    ROLE_SOURCE,
    STATUS_MANUAL,
    STATUS_NOT_ELIGIBLE,
    TENDER_TYPES,
    LegInput,
    Terms,
    calculate_entitlement,
)
from .models import (
    TERMINAL_RECONCILIATION_STATUSES,
    CorporateAction,
    CorporateActionLedgerEntry,
    CorporateActionNotification,
    CorporateActionPosition,
    CorporateActionPositionStatus,
    CorporateActionReconciliation,
    CorporateActionReconciliationStatus,
    CorporateActionStatus,
    CorporateActionType,
)
from .position_adjustment import ledger_sort_key, replay_ledger

logger = logging.getLogger(__name__)

ZERO = Decimal("0")
PositionStatus = CorporateActionPositionStatus
ReconStatus = CorporateActionReconciliationStatus

# Ledger row types that show an event is already accounted for.
EVIDENCE_TYPES = {
    CorporateActionType.BONUS: (TransactionType.BONUS,),
    CorporateActionType.STOCK_DIVIDEND: (TransactionType.BONUS,),
    CorporateActionType.STOCK_SPLIT: (TransactionType.SPLIT,),
    CorporateActionType.REVERSE_SPLIT: (TransactionType.SPLIT,),
    CorporateActionType.ADR_RATIO_CHANGE: (TransactionType.SPLIT,),
    CorporateActionType.CASH_DIVIDEND: (TransactionType.DIVIDEND,),
    # Mergers/demergers: a BUY of the resulting security in the same hierarchy.
    CorporateActionType.MERGER: (TransactionType.BUY,),
    CorporateActionType.DEMERGER: (TransactionType.BUY,),
    # Buyback / tender offer: a SELL at the offer price (checked in _security_matches).
    CorporateActionType.BUYBACK: (TransactionType.SELL,),
    CorporateActionType.TENDER_OFFER: (TransactionType.SELL,),
}
TENDER_PRICE_TOLERANCE = Decimal("0.01")
RESTRUCTURING_TYPES = {CorporateActionType.MERGER: ("MERGER", "FROM"), CorporateActionType.DEMERGER: ("DEMERGER", "PARENT")}
QUANTITY_TYPES = (TransactionType.BUY, TransactionType.SIP, TransactionType.SELL, TransactionType.BONUS, TransactionType.SPLIT)


class ReconciliationError(ValidationError):
    """A decision cannot be applied as requested (shown to the user)."""


class StaleProposalError(ReconciliationError):
    """The proposal changed after it was shown; the user must review it again."""


def is_open_market_buyback(action):
    """True for a buyback made on the exchange, which has no tender to reconcile."""
    return action.action_type == CorporateActionType.BUYBACK and buyback_method(action) == "OPEN_MARKET"


def _norm(value):
    return " ".join(str(value or "").split()).casefold()


def _text(value):
    return " ".join(str(value or "").split())


def _to_decimal(value):
    if value is None or value == "":
        return None
    return value if isinstance(value, Decimal) else Decimal(str(value))


def _sha(material):
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _date(value):
    from datetime import date

    if value is None or isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


# --------------------------------------------------------------------------
# Plan data structures
# --------------------------------------------------------------------------

@dataclass
class Leg:
    asset: Asset
    rows: list
    eligible: dict = field(default_factory=dict)
    pre_event: dict = field(default_factory=dict)


@dataclass
class PlannedTransaction:
    position_key: str
    leg_asset_id: int
    role: str
    transaction_type: str
    transaction_date: object
    quantity: Decimal
    price_per_unit: Decimal
    amount: Decimal
    label: str
    source_key: str
    asset: Asset | None
    asset_label: str
    owner_id: int | None
    family_name: str
    portfolio: str
    asset_class: str
    sub_class: str
    asset_name: str
    underlying: str
    advisors: str

    def as_dict(self):
        return {
            "role": self.role, "transaction_type": self.transaction_type,
            "transaction_date": self.transaction_date.isoformat(),
            "quantity": str(self.quantity), "price_per_unit": str(self.price_per_unit),
            "amount": str(self.amount), "label": self.label,
            "asset_id": self.asset.id if self.asset is not None else None,
            "asset_label": self.asset_label, "underlying": self.underlying,
            "family_name": self.family_name, "portfolio": self.portfolio,
            "asset_class": self.asset_class, "sub_class": self.sub_class,
            "asset_name": self.asset_name, "source_key": self.source_key,
        }


@dataclass
class PositionPlan:
    key: str
    scope_token: str
    family: FamilyGroup | None
    family_name: str
    portfolio: str
    asset_class: str
    sub_class: str
    asset_name: str
    underlying: str
    advisors: str
    legs: list
    status: str = PositionStatus.PENDING
    reason: str = ""
    matched: list = field(default_factory=list)
    result: object = None
    planned: list = field(default_factory=list)

    @property
    def hierarchy_key(self):
        return (self.scope_token, _norm(self.family_name), _norm(self.asset_class),
                _norm(self.sub_class), _norm(self.asset_name))

    @property
    def eligible_quantity(self):
        return sum((leg.eligible["quantity"] for leg in self.legs), ZERO)

    @property
    def cost_basis(self):
        return sum((leg.eligible["invested_value"] for leg in self.legs), ZERO)

    def manual(self, reason):
        self.status = PositionStatus.MANUAL_REVIEW
        self.reason = reason


@dataclass
class ReconciliationPlan:
    action: CorporateAction
    owner: object
    terms: Terms
    cutoff: object
    window: tuple
    positions: list = field(default_factory=list)
    identity_assets: list = field(default_factory=list)
    transactions_checked: int = 0
    notes: list = field(default_factory=list)

    @property
    def actionable(self):
        return [p for p in self.positions if p.status == PositionStatus.PENDING]

    def count(self, status):
        return sum(1 for p in self.positions if p.status == status)

    @property
    def fingerprint(self):
        material = [
            [p.key, p.status, [
                [t.source_key, t.transaction_type, t.transaction_date.isoformat(), str(t.quantity), str(t.amount)]
                for t in p.planned
            ], sorted(tx.id for tx in p.matched)]
            for p in sorted(self.positions, key=lambda item: item.key)
        ]
        return _sha(json.dumps(material, sort_keys=True))


# --------------------------------------------------------------------------
# Scope and identity
# --------------------------------------------------------------------------

def owner_family_ids(owner):
    """Families ``owner`` actually belongs to (never the frontend selection)."""
    return sorted(set(
        FamilyMembership.objects.filter(profile__user=owner).values_list("family_group_id", flat=True)
    ))


def scope_q(owner, family_ids):
    query = Q(owner=owner, family__isnull=True)
    if family_ids:
        query |= Q(family_id__in=family_ids)
    return query


def identity_assets(action, owner, family_ids, approved_asset=None):
    """Asset records that are this security inside the owner's scope."""
    if approved_asset is not None:
        scope = (Q(family_id=approved_asset.family_id) if approved_asset.family_id
                 else Q(owner=owner, family__isnull=True))
        assets = Asset.objects.filter(is_active=True).filter(scope)
        if approved_asset.isin and approved_asset.isin.strip():
            assets = assets.filter(isin__iexact=approved_asset.isin.strip())
        elif approved_asset.symbol and approved_asset.symbol.strip():
            assets = assets.filter(symbol__iexact=approved_asset.symbol.strip())
        else:
            assets = assets.filter(pk=approved_asset.pk)
        found = list(assets)
        if approved_asset.id not in {asset.id for asset in found}:
            found.append(approved_asset)
        return found
    isin = (action.isin or "").strip()
    if not isin:
        return []
    asset_scope = Q(owner=owner, family__isnull=True)
    if family_ids:
        asset_scope |= Q(family_id__in=family_ids)
    # Include Asset rows referenced by in-scope transactions even if the Asset
    # itself carries another family/owner value (legacy imports).
    referenced = Transaction.objects.filter(scope_q(owner, family_ids), asset__isin__iexact=isin).values("asset_id")
    return list(Asset.objects.filter(is_active=True, isin__iexact=isin).filter(asset_scope | Q(id__in=referenced)))


def event_window(action):
    """(start, end) dates in which an uploaded row counts as this event."""
    ex_date = action.ex_date
    record_date = action.record_date or ex_date
    payment_date = action.payment_date or ex_date
    start = min(ex_date, record_date)
    if action.action_type == CorporateActionType.CASH_DIVIDEND and not action.payment_date:
        grace = int(get_pwms_config("corporate_actions", "dividend_match_grace_days", 35))
    elif action.action_type in TENDER_TYPES:
        # The tender opens after the record date and settles weeks later.
        grace = int(get_pwms_config("corporate_actions", "buyback_match_grace_days", 75))
    else:
        grace = int(get_pwms_config("corporate_actions", "uploaded_match_grace_days", 15))
    end = max(ex_date, record_date, payment_date) + timedelta(days=max(0, grace))
    return start, end


def eligibility_cutoff(action):
    """Exclusive trade-date cut-off for entitlement.

    Trades dated before the ex-date are entitled (trade-date ledger). If the
    stored record date precedes the ex-date, the earlier date is used so no
    trade after the record date can qualify.
    """
    if action.record_date and action.record_date < action.ex_date:
        return action.record_date
    return action.ex_date


# --------------------------------------------------------------------------
# Building the plan (read-only)
# --------------------------------------------------------------------------

def _labels(tx):
    asset_name = _text(tx.asset_name) or _text(getattr(tx.asset, "name", ""))
    underlying = _text(tx.underlying) or asset_name
    return {
        "family_name": _text(tx.family_name), "portfolio": _text(tx.portfolio),
        "asset_class": _text(tx.asset_class), "sub_class": _text(tx.sub_class),
        "asset_name": asset_name, "underlying": underlying, "advisors": _text(tx.advisors),
    }


def _scope_token(tx):
    return f"family:{tx.family_id}" if tx.family_id else f"owner:{tx.owner_id}"


def _position_key(scope_token, labels):
    return _sha("|".join([
        scope_token, _norm(labels["family_name"]), _norm(labels["portfolio"]),
        _norm(labels["asset_class"]), _norm(labels["sub_class"]),
        _norm(labels["asset_name"]), _norm(labels["underlying"]),
    ]))


def _group_positions(transactions, families_by_id):
    grouped = defaultdict(list)
    for tx in transactions:
        labels = _labels(tx)
        token = _scope_token(tx)
        grouped[(token, _position_key(token, labels))].append(tx)

    positions = []
    for (token, key), rows in grouped.items():
        rows.sort(key=ledger_sort_key)
        labels = _labels(rows[-1])
        legs_rows = defaultdict(list)
        for tx in rows:
            legs_rows[tx.asset_id].append(tx)
        legs = [Leg(asset=leg_rows[0].asset, rows=leg_rows) for _, leg_rows in sorted(legs_rows.items())]
        positions.append(PositionPlan(
            key=key, scope_token=token, family=families_by_id.get(rows[-1].family_id),
            family_name=labels["family_name"], portfolio=labels["portfolio"],
            asset_class=labels["asset_class"], sub_class=labels["sub_class"],
            asset_name=labels["asset_name"], underlying=labels["underlying"],
            advisors=labels["advisors"], legs=legs,
        ))
    positions.sort(key=lambda p: (_norm(p.family_name), _norm(p.portfolio), _norm(p.asset_class),
                                  _norm(p.sub_class), _norm(p.asset_name), _norm(p.underlying), p.key))
    return positions


def _tender_price_matches(candidate, terms):
    """A SELL counts as the buyback only at the buyback price (within 1%)."""
    price = terms.cash_per_unit
    if not price or price <= ZERO:
        return False
    actual = candidate.price_per_unit
    if not actual and candidate.quantity:
        actual = Decimal(candidate.amount) / Decimal(candidate.quantity)
    if not actual or actual <= ZERO:
        return False
    return abs(Decimal(actual) - price) / price <= TENDER_PRICE_TOLERANCE


def _security_matches(candidate, position, action, terms, identity_ids):
    labels = _labels(candidate)
    if action.action_type in TENDER_TYPES and not _tender_price_matches(candidate, terms):
        return False
    if action.action_type in (CorporateActionType.MERGER, CorporateActionType.DEMERGER):
        candidate_isin = _text(getattr(candidate.asset, "isin", "")).upper()
        if terms.resulting_isin and candidate_isin == terms.resulting_isin:
            return True
        names = {_norm(terms.resulting_security_name), _norm(terms.resulting_symbol)} - {""}
        return bool(names) and _norm(labels["underlying"]) in names
    if candidate.asset_id in identity_ids:
        return True
    if action.isin and _text(getattr(candidate.asset, "isin", "")).upper() == action.isin.strip().upper():
        return True
    return _norm(labels["underlying"]) == _norm(position.underlying)


def _evidence_candidates(action, owner, family_ids, window):
    types = EVIDENCE_TYPES.get(action.action_type)
    if not types:
        return []
    start, end = window
    rows = Transaction.objects.filter(
        scope_q(owner, family_ids),
        transaction_type__in=types,
        transaction_date__gte=start,
        transaction_date__lte=end,
    ).select_related("asset", "corporate_action_entry__position__reconciliation__action")
    identity = action.event_identity
    candidates = []
    for tx in rows:
        # A row booked by PWMS for a *different* corporate action is never
        # evidence for this one (e.g. an interim and a final dividend close together).
        entry = tx.corporate_action_entry if hasattr(tx, "corporate_action_entry") else None
        if entry is not None and entry.position.reconciliation.action.event_identity != identity:
            continue
        candidates.append(tx)
    return candidates


def _match_evidence(plan, candidates):
    """Attach already-recorded ledger rows to positions (see module docstring)."""
    action, terms = plan.action, plan.terms
    identity_ids = {asset.id for asset in plan.identity_assets}
    by_hierarchy = defaultdict(list)
    for candidate in candidates:
        labels = _labels(candidate)
        by_hierarchy[(_scope_token(candidate), _norm(labels["family_name"]), _norm(labels["asset_class"]),
                      _norm(labels["sub_class"]), _norm(labels["asset_name"]))].append(candidate)
    # Only positions with an entitlement need (or compete for) evidence.
    eligible = [position for position in plan.positions if position.eligible_quantity > ZERO]
    siblings = defaultdict(list)
    for position in eligible:
        siblings[position.hierarchy_key].append(position)

    def distance(tx):
        return (abs((tx.transaction_date - action.ex_date).days), tx.id)

    used = set()
    # Pass 1: exact portfolio label.
    for position in eligible:
        pool = [c for c in by_hierarchy.get(position.hierarchy_key, [])
                if _security_matches(c, position, action, terms, identity_ids)]
        exact = sorted((c for c in pool if _norm(c.portfolio) == _norm(position.portfolio)), key=distance)
        if exact:
            position.matched = exact
            used.update(tx.id for tx in exact)
    # Pass 2: a differently-labelled portfolio, only when it is unambiguous.
    for position in eligible:
        if position.matched or position.status != PositionStatus.PENDING:
            continue
        group = siblings[position.hierarchy_key]
        sibling_portfolios = {_norm(item.portfolio) for item in group}
        pool = sorted((c for c in by_hierarchy.get(position.hierarchy_key, [])
                       if c.id not in used and _security_matches(c, position, action, terms, identity_ids)
                       and _norm(c.portfolio) not in sibling_portfolios), key=distance)
        if not pool:
            continue
        if len(group) == 1:
            position.matched = pool
            used.update(tx.id for tx in pool)
        else:
            position.manual(
                "An uploaded corporate-action transaction exists for this family member and asset hierarchy "
                f"(transaction #{pool[0].id}, portfolio '{_text(pool[0].portfolio) or '-'}'), but its portfolio label "
                "does not identify which of the matching portfolios it belongs to. Review it before booking."
            )


def _match_restructurings(plan, family_ids):
    kind_role = RESTRUCTURING_TYPES.get(plan.action.action_type)
    if not kind_role or not family_ids:
        return
    kind, role = kind_role
    start, end = plan.window
    pending_rows = list(PendingRestructuring.objects.filter(
        family_id__in=family_ids, kind=kind, event_date__gte=start, event_date__lte=end,
    ).exclude(status="REJECTED"))
    if not pending_rows:
        return
    isin = (plan.action.isin or "").strip().upper()
    for position in plan.positions:
        if position.status != PositionStatus.PENDING or position.matched or position.family is None:
            continue
        for pending in pending_rows:
            if pending.family_id != position.family.id:
                continue
            if _norm(pending.family_name) != _norm(position.family_name) or _norm(pending.asset_name) != _norm(position.asset_name):
                continue
            legs = [leg for leg in (pending.legs or []) if leg.get("role") == role]
            if not any((isin and _text(leg.get("isin")).upper() == isin)
                       or _norm(leg.get("underlying")) == _norm(position.underlying) for leg in legs):
                continue
            if pending.status == "APPLIED":
                applied = list(Transaction.objects.filter(id__in=pending.applied_transaction_ids or []))
                position.matched = applied
                position.status = PositionStatus.ALREADY_RECORDED
                position.reason = f"Recorded by the approved restructuring upload #{pending.id}."
            else:
                position.manual(
                    f"The uploaded {kind.lower()} for this holding is awaiting review as restructuring #{pending.id}. "
                    "Approve or reject it with `python manage.py review_restructurings` first; it is not treated as booked."
                )
            break


def _resolve_result_asset(plan, position):
    isin = plan.terms.resulting_isin
    if not isin:
        return None
    query = Asset.objects.filter(is_active=True, isin__iexact=isin)
    if position.family is not None:
        query = query.filter(family=position.family)
    else:
        query = query.filter(owner=plan.owner, family__isnull=True)
    return query.order_by("id").first()


def _result_label(terms):
    return terms.resulting_security_name or terms.resulting_symbol or terms.resulting_isin


def _plan_transactions(plan, position):
    action, terms = plan.action, plan.terms
    legs = {str(leg.asset.id): leg for leg in position.legs}
    result_asset = None
    if any(entry.role == ROLE_RESULT for entry in position.result.entries):
        result_asset = _resolve_result_asset(plan, position)
    planned = []
    for index, entry in enumerate(position.result.entries):
        leg = legs[entry.leg_key]
        latest = leg.rows[-1]
        if entry.date_field == "payment_date" and action.payment_date:
            tx_date = action.payment_date
        else:
            tx_date = action.ex_date
        if entry.role == ROLE_SOURCE:
            asset, asset_label, underlying = leg.asset, leg.asset.name, position.underlying
        else:
            asset, asset_label = result_asset, _result_label(terms)
            underlying = (result_asset.name if result_asset is not None else "") or _result_label(terms)
        planned.append(PlannedTransaction(
            position_key=position.key, leg_asset_id=leg.asset.id, role=entry.role,
            transaction_type=entry.transaction_type, transaction_date=tx_date,
            quantity=entry.quantity, price_per_unit=entry.price_per_unit, amount=entry.amount,
            label=entry.label,
            source_key=_sha(
                f"corporate-action-ledger:{action.event_identity}:{position.scope_token}:{position.key}:"
                f"{leg.asset.id}:{entry.role}:{entry.transaction_type}:{index}"
            ),
            asset=asset, asset_label=asset_label, owner_id=latest.owner_id or plan.owner.id,
            family_name=position.family_name, portfolio=position.portfolio,
            asset_class=position.asset_class, sub_class=position.sub_class,
            asset_name=position.asset_name, underlying=underlying, advisors=_text(latest.advisors),
        ))
    return planned


def build_plan(action, owner, *, approved_asset=None, accepted_quantities=None):
    """Read-only reconciliation proposal for ``owner``. Never writes anything.

    ``accepted_quantities`` ({position_key: Decimal}) is used only when a buyback
    or tender offer is accepted: the shares the company accepted per position.
    A position given 0 was not tendered and is marked rejected.
    """
    for attr in ("old_units", "new_units", "cash_per_unit", "cost_allocation_ratio", "cash_in_lieu_price"):
        setattr(action, attr, _to_decimal(getattr(action, attr, None)))
    for attr in ("ex_date", "record_date", "payment_date"):
        setattr(action, attr, _date(getattr(action, attr, None)))
    family_ids = owner_family_ids(owner)
    terms = Terms.from_action(action)
    plan = ReconciliationPlan(
        action=action, owner=owner, terms=terms,
        cutoff=eligibility_cutoff(action), window=event_window(action),
    )
    plan.identity_assets = identity_assets(action, owner, family_ids, approved_asset=approved_asset)
    if not plan.identity_assets:
        return plan
    transactions = list(
        Transaction.objects.filter(scope_q(owner, family_ids), asset__in=plan.identity_assets)
        .select_related("asset", "family")
    )
    plan.transactions_checked = len(transactions)
    families_by_id = {family.id: family for family in FamilyGroup.objects.filter(id__in=family_ids)}
    plan.positions = _group_positions(transactions, families_by_id)

    for position in plan.positions:
        for leg in position.legs:
            leg.eligible = replay_ledger(leg.rows, before=plan.cutoff)
            leg.pre_event = replay_ledger(leg.rows, before=action.ex_date)
    # A group with no ledger history before the cut-off was opened after the
    # event (or consists only of uploaded event rows): it is not a position
    # for this event at all, and must not compete for evidence.
    plan.positions = [
        position for position in plan.positions
        if any(tx.transaction_date < plan.cutoff for leg in position.legs for tx in leg.rows)
    ]

    candidates = _evidence_candidates(action, owner, family_ids, plan.window)
    plan.transactions_checked += len(candidates)
    _match_evidence(plan, candidates)
    for position in plan.positions:
        if position.matched and position.status == PositionStatus.PENDING:
            position.status = PositionStatus.ALREADY_RECORDED
            dates = sorted({tx.transaction_date.isoformat() for tx in position.matched})
            position.reason = (
                f"Already recorded by existing transaction(s) #{', #'.join(str(tx.id) for tx in position.matched)} "
                f"dated {', '.join(dates)}. No additional transaction is required and the existing rows are not changed."
            )
    _match_restructurings(plan, family_ids)

    blocking = []
    if action.status == CorporateActionStatus.REJECTED:
        blocking.append("This event is marked rejected/cancelled; nothing will be booked for it.")
    conflicts = (action.metadata or {}).get("term_conflicts") if isinstance(action.metadata, dict) else None
    if conflicts:
        blocking.append(
            "The data provider reported terms that differ from the stored terms ("
            + "; ".join(f"{name}: stored {values.get('stored')}, provider {values.get('provider')}"
                        for name, values in sorted(conflicts.items()))
            + "). Verify the official terms and update the event."
        )

    open_market = is_open_market_buyback(action)
    for position in plan.positions:
        if position.status != PositionStatus.PENDING:
            continue
        legs = [LegInput(key=str(leg.asset.id), quantity=leg.eligible["quantity"], cost=leg.eligible["invested_value"])
                for leg in position.legs]
        if open_market and position.eligible_quantity > ZERO:
            position.status = PositionStatus.NOT_ELIGIBLE
            position.reason = (
                "Open-market buyback: the company buys shares on the exchange, so there is no tender to record. "
                "Any shares you sold appear as normal sales in your uploads."
            )
            continue
        accepted = None
        if accepted_quantities is not None and action.action_type in TENDER_TYPES:
            accepted = accepted_quantities.get(position.key)
            if accepted is not None and accepted == ZERO:
                position.status = PositionStatus.REJECTED
                position.reason = "Not tendered (you said these shares were not tendered or not accepted)."
                continue
        position.result = calculate_entitlement(terms, legs, accepted_quantity=accepted, event_date=action.ex_date)
        if position.result.status == STATUS_NOT_ELIGIBLE:
            position.status = PositionStatus.NOT_ELIGIBLE
            position.reason = position.result.reason
            continue
        if blocking:
            position.manual(" ".join(blocking))
            continue
        if position.result.status == STATUS_MANUAL:
            position.manual(position.result.reason)
            continue
        if action.action_type in (CorporateActionType.MERGER, CorporateActionType.DEMERGER):
            if position.eligible_quantity != sum((leg.pre_event["quantity"] for leg in position.legs), ZERO):
                position.manual(
                    "Transactions dated between the record date and the ex-date change this holding; "
                    "the converted quantity must be reviewed manually."
                )
                continue
        if action.action_type == CorporateActionType.MERGER:
            later = [tx for leg in position.legs for tx in leg.rows
                     if tx.transaction_date >= action.ex_date and tx.transaction_type in QUANTITY_TYPES]
            if later:
                position.manual(
                    "Transactions in the original security are dated on or after the merger date "
                    f"(#{', #'.join(str(tx.id) for tx in later[:5])}); the conversion may already be recorded "
                    "in another form. Review before booking."
                )
                continue
        position.planned = _plan_transactions(plan, position)
        if not position.planned and not position.result.needs_accepted_quantity:
            position.manual("The calculation produced no ledger entries.")
    return plan


# --------------------------------------------------------------------------
# Persisting the proposal
# --------------------------------------------------------------------------

def _position_defaults(plan, position):
    """Field values saved on the CorporateActionPosition row for one planned position."""
    result = position.result
    calculation = {
        "action_type": plan.action.action_type,
        "ratio": plan.terms.ratio_text,
        "old_units": str(plan.terms.old_units) if plan.terms.old_units is not None else None,
        "new_units": str(plan.terms.new_units) if plan.terms.new_units is not None else None,
        "cash_per_unit": str(plan.terms.cash_per_unit) if plan.terms.cash_per_unit is not None else None,
        "cost_allocation_ratio": (str(plan.terms.cost_allocation_ratio)
                                  if plan.terms.cost_allocation_ratio is not None else None),
        "cash_in_lieu_price": (str(plan.terms.cash_in_lieu_price)
                               if plan.terms.cash_in_lieu_price is not None else None),
        "resulting_security": _result_label(plan.terms) or None,
        "eligibility_rule": (
            f"Trades dated before {plan.cutoff.isoformat()} are entitled"
            + (" (the record date precedes the ex-date, so the earlier date is used)."
               if plan.cutoff != plan.action.ex_date else " (ex-date rule).")
        ),
        "explanation": result.explanation if result is not None else "",
        "cash_in_lieu_pending": bool(result and result.cash_in_lieu_pending),
        "needs_accepted_quantity": bool(result and result.needs_accepted_quantity),
        "legs": [
            {"asset_id": leg.asset.id, "asset_name": leg.asset.name,
             "eligible_quantity": str(leg.eligible["quantity"]),
             "cost_basis": str(leg.eligible["invested_value"])}
            for leg in position.legs
        ],
        "matched_transactions": [
            {"id": tx.id, "transaction_type": tx.transaction_type, "transaction_date": tx.transaction_date.isoformat(),
             "quantity": str(tx.quantity), "amount": str(tx.amount), "source": tx.source,
             "portfolio": _text(tx.portfolio)}
            for tx in position.matched
        ],
    }

    return {
        "family": position.family,
        "status": position.status,
        "family_name": position.family_name[:255], "portfolio": position.portfolio[:255],
        "asset_class": position.asset_class[:255], "sub_class": position.sub_class[:255],
        "asset_name": position.asset_name[:255], "underlying": position.underlying[:255],
        "advisors": position.advisors[:255],
        "asset": position.legs[0].asset if position.legs else None,
        "asset_ids": [leg.asset.id for leg in position.legs],
        "eligibility_cutoff": plan.cutoff,
        "eligible_quantity": position.eligible_quantity,
        "quantity_delta": result.quantity_delta if result else None,
        "resulting_quantity": result.resulting_quantity if result else None,
        "new_security_quantity": result.new_security_quantity if result else None,
        "fractional_entitlement": (result.fractional_entitlement if result and result.fractional_entitlement else None),
        "cost_basis": position.cost_basis.quantize(Decimal("0.0001")),
        "resulting_cost_basis": (result.resulting_cost_basis.quantize(Decimal("0.0001"))
                                 if result and result.resulting_cost_basis is not None else None),
        "allocated_cost": (result.allocated_cost if result and result.allocated_cost is not None else None),
        "cash_amount": (result.cash_amount if result and result.cash_amount is not None else None),
        "matched_transaction_ids": [tx.id for tx in position.matched],
        "planned_entries": [item.as_dict() for item in position.planned],
        "calculation": calculation,
        "reason": position.reason,
    }


def plan_status(plan, *, has_indirect=False):
    """(status, notes) for a non-terminal reconciliation."""
    if is_open_market_buyback(plan.action) and not plan.actionable:
        return ReconStatus.MANUAL_REVIEW, (
            "Open-market buyback: there is no tender to record, so nothing will be booked. "
            "Shares you sold on the exchange are normal sales in your uploads."
        )
    if not plan.positions:
        if has_indirect:
            return ReconStatus.MANUAL_REVIEW, (
                "Indirect mutual-fund/PMS underlying exposure only. Do not create a direct stock transaction."
            )
        return ReconStatus.MANUAL_REVIEW, "No direct holding of this security was found in your ledger."
    pending = plan.count(PositionStatus.PENDING)
    manual = plan.count(PositionStatus.MANUAL_REVIEW)
    recorded = plan.count(PositionStatus.ALREADY_RECORDED)
    if pending:
        notes = (
            f"{pending} eligible position(s) can be accepted"
            + (f"; {recorded} already recorded" if recorded else "")
            + (f"; {manual} need manual review" if manual else "")
            + ". Nothing is booked until you accept."
        )
        return ReconStatus.PENDING, notes
    if manual:
        return ReconStatus.MANUAL_REVIEW, (
            f"{manual} position(s) need manual review"
            + (f"; {recorded} already recorded" if recorded else "") + ". Nothing has been booked."
        )
    if recorded:
        return ReconStatus.ALREADY_RECORDED, (
            f"All {recorded} eligible position(s) already contain this corporate action in the uploaded "
            "transactions. No additional transaction is required."
        )
    return ReconStatus.MANUAL_REVIEW, (
        "No positive eligible direct holding was established immediately before the ex-date. "
        "No automatic position adjustment will be applied."
    )


def should_notify(plan, status, *, has_indirect=False):
    if is_open_market_buyback(plan.action) and not plan.actionable:
        return False
    if status != ReconStatus.MANUAL_REVIEW:
        return True
    if has_indirect:
        return True
    return any(p.status == PositionStatus.MANUAL_REVIEW for p in plan.positions)


def persist_plan(reconciliation, plan, *, has_indirect=False, set_status=True):
    """Store the proposal on a non-terminal reconciliation. Ledger untouched."""
    keys = set()
    for position in plan.positions:
        keys.add(position.key)
        CorporateActionPosition.objects.update_or_create(
            reconciliation=reconciliation, position_key=position.key,
            defaults=_position_defaults(plan, position),
        )
    reconciliation.positions.exclude(position_key__in=keys).exclude(
        status=PositionStatus.APPLIED
    ).delete()
    update_fields = ["proposal_fingerprint", "last_reconciled_at", "suggested_quantity",
                     "matched_transaction_ids", "updated_at"]
    reconciliation.proposal_fingerprint = plan.fingerprint
    reconciliation.last_reconciled_at = timezone.now()
    actionable = plan.actionable
    reconciliation.suggested_quantity = (
        sum((p.eligible_quantity for p in actionable), ZERO) if actionable
        else (sum((p.eligible_quantity for p in plan.positions), ZERO) if plan.positions else None)
    )
    reconciliation.matched_transaction_ids = sorted({tx.id for p in plan.positions for tx in p.matched})[:200]
    status, notes = plan_status(plan, has_indirect=has_indirect)
    if set_status:
        reconciliation.status = status
        reconciliation.notes = notes
        update_fields += ["status", "notes"]
    reconciliation.save(update_fields=update_fields)
    return status


def approved_identity(reconciliation, action):
    """The holding the user approved for an event without an ISIN (buyback awaiting quantities)."""
    if (action.isin or "").strip() or not reconciliation.selected_asset_id:
        return None
    return Asset.objects.filter(pk=reconciliation.selected_asset_id).first()


def refresh_reconciliation(reconciliation):
    """Re-calculate a non-terminal reconciliation. Never writes ledger rows."""
    if is_terminal(reconciliation) or reconciliation.status == ReconStatus.PENDING_USER_APPROVAL:
        return reconciliation
    action = reconciliation.action
    fill_terms_from_announcements(action)
    plan = build_plan(action, reconciliation.owner, approved_asset=approved_identity(reconciliation, action))
    has_indirect = reconciliation.action.exposures.filter(
        owner=reconciliation.owner, exposure_type__in=("MUTUAL_FUND", "PMS")
    ).exists()
    persist_plan(reconciliation, plan, has_indirect=has_indirect)
    return reconciliation


def is_terminal(reconciliation):
    if reconciliation.status in TERMINAL_RECONCILIATION_STATUSES:
        return True
    # A user's own "already recorded" decision is final; a system one is re-derived.
    return reconciliation.status == ReconStatus.ALREADY_RECORDED and reconciliation.reviewed_at is not None


# --------------------------------------------------------------------------
# Applying an accepted proposal (the only ledger writer)
# --------------------------------------------------------------------------

def _lock_scope(owner, plan):
    family_ids = sorted({p.family.id for p in plan.positions if p.family is not None})
    if family_ids:
        list(FamilyGroup.objects.select_for_update().filter(id__in=family_ids).order_by("id"))
    if any(p.family is None for p in plan.positions):
        type(owner).objects.select_for_update().filter(pk=owner.pk).first()


def _create_result_asset(plan, position, planned):
    from investments.services.transaction_import import TransactionImporter

    asset = TransactionImporter._get_or_create_asset(
        owner=plan.owner, family=position.family,
        asset_name=_result_label(plan.terms), isin=plan.terms.resulting_isin,
        asset_class=planned.asset_class, sub_class=planned.sub_class, allow_name_match=False,
    )
    if plan.terms.resulting_symbol and not asset.symbol:
        asset.symbol = plan.terms.resulting_symbol
        asset.save(update_fields=["symbol", "updated_at"])
    return asset


def _audit_note(plan, position, planned, approver, reconciliation):
    action = plan.action
    identity = action.isin or action.symbol
    parts = [
        f"Corporate action #{action.id}: {action.get_action_type_display()} of {action.security_name} [{identity}]",
        f"ex-date {action.ex_date.isoformat()}"
        + (f", record date {action.record_date.isoformat()}" if action.record_date else "")
        + (f", payment date {action.payment_date.isoformat()}" if action.payment_date else ""),
        planned.label,
    ]
    if plan.terms.ratio_text:
        parts.append(f"ratio {plan.terms.ratio_text}")
    result = position.result
    if result is not None:
        parts.append(
            f"eligible quantity {result.eligible_quantity.normalize():f}"
            + (f", resulting quantity {result.resulting_quantity.normalize():f}" if result.resulting_quantity is not None else "")
        )
        if result.explanation:
            parts.append(result.explanation)
    parts.append(
        f"hierarchy {position.family_name} / {position.portfolio} / {position.asset_class} / "
        f"{position.sub_class} / {position.asset_name} / {position.underlying}"
    )
    parts.append(f"source {action.source}" + (f" {action.source_reference}" if action.source_reference else ""))
    parts.append(
        f"accepted by {approver.get_username()} on {timezone.now().isoformat(timespec='seconds')} "
        f"(reconciliation #{reconciliation.id})"
    )
    return "; ".join(parts)[:4000]


def _description(plan, planned):
    action = plan.action
    text = f"{planned.label} — {action.get_action_type_display()}"
    if plan.terms.ratio_text and action.action_type != CorporateActionType.CASH_DIVIDEND:
        text += f" {plan.terms.ratio_text}"
    if (action.action_type == CorporateActionType.CASH_DIVIDEND or action.action_type in TENDER_TYPES) \
            and plan.terms.cash_per_unit is not None:
        text += f" @ {plan.terms.cash_per_unit.normalize():f}/share"
    text += f", ex-date {action.ex_date.isoformat()} ({action.security_name})"
    return text[:500]


def _apply_positions(plan, reconciliation, approver):
    """Create the missing ledger rows for every PENDING position. Returns counters."""
    from portfolio.services.holding_engine import HoldingCalculationEngine
    from portfolio.services.portfolio_position_engine import PortfolioPositionEngine

    created_ids, reused_ids, recorded_ids = [], [], []
    touched_assets = {}
    touched_positions = set()
    rows_by_key = {row.position_key: row for row in reconciliation.positions.all()}
    for position in plan.positions:
        if position.status == PositionStatus.ALREADY_RECORDED:
            recorded_ids.extend(tx.id for tx in position.matched)
            continue
        if position.status != PositionStatus.PENDING:
            continue
        row = rows_by_key.get(position.key)
        position_created = []
        for planned in position.planned:
            asset = planned.asset
            if asset is None:
                asset = _create_result_asset(plan, position, planned)
                planned.asset = asset
                if planned.role == ROLE_RESULT and not planned.underlying:
                    planned.underlying = asset.name
            defaults = {
                "owner_id": planned.owner_id, "family": position.family,
                "family_name": planned.family_name, "portfolio": planned.portfolio,
                "asset_class": planned.asset_class, "sub_class": planned.sub_class,
                "asset_name": planned.asset_name, "underlying": planned.underlying,
                "advisors": planned.advisors, "asset": asset,
                "transaction_type": planned.transaction_type,
                "transaction_date": planned.transaction_date,
                "quantity": planned.quantity, "price_per_unit": planned.price_per_unit,
                "amount": planned.amount, "fees": ZERO,
                "notes": _audit_note(plan, position, planned, approver, reconciliation),
            }
            tx, was_created = Transaction.objects.get_or_create(
                family=position.family, source=TransactionSource.OTHER, source_key=planned.source_key,
                defaults=defaults,
            )
            if was_created:
                created_ids.append(tx.id)
                if row is not None:
                    CorporateActionLedgerEntry.objects.get_or_create(
                        transaction=tx,
                        defaults={"position": row, "role": planned.role, "description": _description(plan, planned)},
                    )
            else:
                reused_ids.append(tx.id)
            position_created.append(tx.id)
            touched_assets[tx.asset_id] = tx.asset
            if position.family is not None:
                touched_positions.add((position.family.id, tx.family_name, tx.portfolio, tx.asset_id))
        if row is not None:
            row.status = PositionStatus.APPLIED
            row.created_transaction_ids = position_created
            row.applied_at = timezone.now()
            row.applied_by = approver
            row.save(update_fields=["status", "created_transaction_ids", "applied_at", "applied_by", "updated_at"])

    for asset in touched_assets.values():
        HoldingCalculationEngine.rebuild_holding(Asset.objects.select_related("family", "owner").get(pk=asset.pk))
    families = {family.id: family for family in FamilyGroup.objects.filter(id__in={key[0] for key in touched_positions})}
    for family_id, family_name, portfolio, asset_id in sorted(touched_positions, key=lambda item: (item[0], str(item[1]), str(item[2]), item[3])):
        PortfolioPositionEngine.rebuild_position(
            family_name=family_name, portfolio=portfolio, asset=touched_assets[asset_id], family=families[family_id],
        )
    return {"created": created_ids, "reused": reused_ids, "recorded": recorded_ids}


def _refresh_siblings(action, exclude_id):
    """Other users' reconciliations of the same event re-check against the new ledger rows."""
    lookup = {"action__action_type": action.action_type, "action__ex_date": action.ex_date}
    if action.isin:
        lookup["action__isin__iexact"] = action.isin
    else:
        lookup.update({"action__isin": "", "action__symbol__iexact": action.symbol})
    siblings = CorporateActionReconciliation.objects.filter(**lookup).exclude(id=exclude_id).select_related("action", "owner")
    for sibling in siblings:
        try:
            refresh_reconciliation(sibling)
        except Exception:  # noqa: BLE001 - a sibling refresh must never break an acceptance
            logger.exception("Could not refresh corporate-action reconciliation %s", sibling.id)


def _locked_reconciliation(owner, reconciliation_id):
    reconciliation = (
        CorporateActionReconciliation.objects.select_for_update()
        .filter(owner=owner, id=reconciliation_id).first()
    )
    if reconciliation is None:
        raise LookupError("Reconciliation not found.")
    return reconciliation


def accept_reconciliation(*, owner, reconciliation_id, fingerprint=None, notes="", accepted_quantities=None):
    """Book the accepted proposal into the ledger. Idempotent and concurrency-safe.

    When the proposal changed since it was shown, or nothing is bookable, the
    refreshed proposal is committed first and the error raised afterwards, so
    the user sees the new calculation; no ledger row is written in that case.
    For a buyback or tender offer, ``accepted_quantities`` maps each pending
    position's id to the number of shares the company accepted (0 = not tendered).
    """
    outcome = _accept_under_lock(owner=owner, reconciliation_id=reconciliation_id, fingerprint=fingerprint,
                                 notes=notes, accepted_quantities=accepted_quantities)
    if isinstance(outcome, ReconciliationError):
        raise outcome
    return outcome


def _accepted_by_key(reconciliation, plan, accepted_quantities):
    """Validate the accepted buyback quantities; returns {position_key: Decimal}."""
    waiting = [p for p in plan.actionable if p.result is not None and p.result.needs_accepted_quantity]
    if not waiting:
        return {}
    if not isinstance(accepted_quantities, dict) or not accepted_quantities:
        raise ReconciliationError(
            "Enter how many shares were accepted for each holding (0 if that holding did not tender)."
        )
    keys_by_id = {str(row_id): key for row_id, key in reconciliation.positions.values_list("id", "position_key")}
    by_key = {}
    for raw_id, raw_quantity in accepted_quantities.items():
        key = keys_by_id.get(str(raw_id))
        if key is None:
            raise ReconciliationError(f"Holding #{raw_id} is not part of this corporate action.")
        try:
            quantity = Decimal(str(raw_quantity).strip())
        except (InvalidOperation, ValueError):
            raise ReconciliationError(f"Accepted quantity for holding #{raw_id} must be a number.") from None
        if not quantity.is_finite() or quantity < ZERO or quantity != quantity.to_integral_value():
            raise ReconciliationError(f"Accepted quantity for holding #{raw_id} must be a whole number, 0 or more.")
        by_key[key] = quantity
    for position in waiting:
        if position.key not in by_key:
            raise ReconciliationError(
                f"Enter the accepted quantity for {position.family_name or 'each holding'} / "
                f"{position.portfolio or '-'} (0 if not tendered)."
            )
        if by_key[position.key] > position.eligible_quantity:
            raise ReconciliationError(
                f"{position.family_name or 'This holding'} held {position.eligible_quantity.normalize():f} shares on the "
                f"record date; the accepted quantity cannot be more than that."
            )
    if all(by_key[p.key] == ZERO for p in waiting):
        raise ReconciliationError("Every quantity is 0. Choose \"Did not tender\" instead; nothing will be booked.")
    return {key: value for key, value in by_key.items() if key in {p.key for p in waiting}}


def _accept_under_lock(*, owner, reconciliation_id, fingerprint=None, notes="", accepted_quantities=None):
    """Locked, atomic acceptance. Returns (reconciliation, counters) or an error to raise after commit."""
    with db_transaction.atomic():
        reconciliation = _locked_reconciliation(owner, reconciliation_id)
        if reconciliation.status == ReconStatus.COMPLETED:
            return reconciliation, {"created": [], "reused": [], "recorded": []}
        if reconciliation.status in (ReconStatus.REJECTED, ReconStatus.DISMISSED):
            raise ReconciliationError("This corporate action was rejected; it cannot be accepted.")
        if reconciliation.status == ReconStatus.PENDING_USER_APPROVAL:
            raise ReconciliationError("Approve the security identity first.")
        if reconciliation.status == ReconStatus.APPROVED:
            raise ReconciliationError("This corporate action has already been approved and booked.")
        action = CorporateAction.objects.get(pk=reconciliation.action_id)
        approved = approved_identity(reconciliation, action)
        plan = build_plan(action, owner, approved_asset=approved)
        _lock_scope(owner, plan)
        plan = build_plan(action, owner, approved_asset=approved)  # re-read under the scope lock
        has_indirect = action.exposures.filter(owner=owner, exposure_type__in=("MUTUAL_FUND", "PMS")).exists()
        if fingerprint and fingerprint != plan.fingerprint:
            persist_plan(reconciliation, plan, has_indirect=has_indirect)
            return StaleProposalError(
                "The calculation changed since it was shown (new transactions or event terms). "
                "Review the refreshed proposal and accept again."
            )
        if not plan.actionable:
            persist_plan(reconciliation, plan, has_indirect=has_indirect)
            return ReconciliationError("There is no eligible position to book for this corporate action.")
        if action.action_type in TENDER_TYPES:
            quantities = _accepted_by_key(reconciliation, plan, accepted_quantities)
            if quantities:
                plan = build_plan(action, owner, approved_asset=approved, accepted_quantities=quantities)
                problems = [p.reason for p in plan.positions
                            if p.key in quantities and p.status == PositionStatus.MANUAL_REVIEW]
                if problems:
                    raise ReconciliationError(" ".join(sorted(set(problems))))
        persist_plan(reconciliation, plan, has_indirect=has_indirect, set_status=False)
        counters = _apply_positions(plan, reconciliation, owner)
        manual = plan.count(PositionStatus.MANUAL_REVIEW)
        not_tendered = plan.count(PositionStatus.REJECTED)
        reconciliation.status = ReconStatus.COMPLETED
        reconciliation.reviewed_at = timezone.now()
        reconciliation.reviewed_by = owner
        reconciliation.matched_transaction_ids = sorted(set(counters["created"] + counters["reused"] + counters["recorded"]))
        summary = (
            f"Accepted: created {len(counters['created'])} ledger transaction(s)"
            + (f", reused {len(counters['reused'])} identical existing row(s)" if counters["reused"] else "")
            + (f", {len(counters['recorded'])} already-recorded row(s) left unchanged" if counters["recorded"] else "")
            + (f"; {not_tendered} holding(s) not tendered" if not_tendered else "")
            + (f"; {manual} position(s) still need manual review and were not booked" if manual else "")
            + "."
        )
        reconciliation.notes = (f"{summary} {notes}".strip())[:5000]
        reconciliation.save(update_fields=[
            "status", "reviewed_at", "reviewed_by", "matched_transaction_ids", "notes", "updated_at",
        ])
        CorporateActionNotification.objects.filter(owner=owner, action=action).update(
            reviewed=True, updated_at=timezone.now(),
        )
        db_transaction.on_commit(lambda: _refresh_siblings(action, reconciliation.id))
    return reconciliation, counters


def reject_reconciliation(*, owner, reconciliation_id, reason=""):
    """Record a rejection. Never writes ledger rows; never reopened by later scans."""
    with db_transaction.atomic():
        reconciliation = _locked_reconciliation(owner, reconciliation_id)
        if reconciliation.status == ReconStatus.REJECTED:
            return reconciliation
        if reconciliation.status in (ReconStatus.COMPLETED, ReconStatus.APPROVED):
            raise ReconciliationError(
                "This corporate action is already booked in the ledger. Delete or edit those transactions instead."
            )
        reconciliation.status = ReconStatus.REJECTED
        reconciliation.notes = (str(reason or "").strip() or "Rejected by the user. No transaction was changed.")[:5000]
        reconciliation.reviewed_at = timezone.now()
        reconciliation.reviewed_by = owner
        reconciliation.save(update_fields=["status", "notes", "reviewed_at", "reviewed_by", "updated_at"])
        reconciliation.positions.exclude(status=PositionStatus.APPLIED).update(
            status=PositionStatus.REJECTED, updated_at=timezone.now(),
        )
        CorporateActionNotification.objects.filter(owner=owner, action=reconciliation.action_id).update(
            reviewed=True, dismissed=True, updated_at=timezone.now(),
        )
    return reconciliation


def apply_identity_approval(*, owner, reconciliation, asset):
    """Book an approved no-ISIN identity (existing identity-approval workflow).

    Called inside the caller's atomic block with the reconciliation locked.
    Returns (matched_ids, created_count, skipped_count), or None for a buyback /
    tender offer, which becomes a normal proposal that asks for the accepted quantity.
    """
    action = reconciliation.action
    plan = build_plan(action, owner, approved_asset=asset)
    _lock_scope(owner, plan)
    plan = build_plan(action, owner, approved_asset=asset)
    if action.action_type in TENDER_TYPES:
        # A buyback still needs the accepted quantity: approving the identity only
        # turns it into an ordinary proposal; nothing is booked here.
        persist_plan(reconciliation, plan)
        return None
    persist_plan(reconciliation, plan, set_status=False)
    actionable = plan.actionable
    recorded = [p for p in plan.positions if p.status == PositionStatus.ALREADY_RECORDED]
    if not actionable and not recorded:
        manual = [p.reason for p in plan.positions if p.status == PositionStatus.MANUAL_REVIEW]
        if manual:
            raise ReconciliationError("This holding needs manual review: " + " ".join(sorted(set(manual))))
        if action.action_type in (CorporateActionType.BONUS, CorporateActionType.STOCK_DIVIDEND):
            raise ReconciliationError(
                "No positive whole-share bonus entitlement was found across the record-date portfolio hierarchy."
            )
        raise ReconciliationError("No positive eligible entitlement was found across the portfolio hierarchy.")
    counters = _apply_positions(plan, reconciliation, owner)
    matched = sorted(set(counters["created"] + counters["reused"] + counters["recorded"]))
    skipped = len(recorded) + len(counters["reused"])
    db_transaction.on_commit(lambda: _refresh_siblings(action, reconciliation.id))
    return matched, len(counters["created"]), skipped
