"""Mergers and demergers found in transaction uploads.

``*Merger``/``*Merged`` and ``*DeMerger``/``*DeMerger (New)`` rows are
corporate actions, not transactions. The importer does not import them: it
holds them in ``PendingRestructuring`` (``hold_rows``). Nothing is created
until the user runs ``review_restructurings --approve`` (``apply``).

Approval carries the cost of the old holding to the new one using only
existing transaction types, so no calculation changes:

* merger: SELL the old stock at its own average cost (no realized gain) and
  BUY the new stock at that same amount. Same-day, equal and opposite, so
  XIRR is unchanged and the cost basis moves with the shares.
* demerger: the parent is sold and bought back at its reduced cost, and each
  new company is bought at its share of the cost, chosen by the user from the
  company's cost-allocation notice. The amounts net to zero.

The original purchase dates are not carried over (the new lots are dated at the
event), which matters only for holding-period views.
"""
import hashlib
import logging
import re
from collections import defaultdict
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from django.db import transaction as db_transaction
from django.utils import timezone

logger = logging.getLogger(__name__)

MERGER = "MERGER"
DEMERGER = "DEMERGER"

_TYPES = {
    "*MERGER": (MERGER, "TO"),
    "*MERGED": (MERGER, "FROM"),
    "*DEMERGER": (DEMERGER, "PARENT"),
    "*DEMERGER (NEW)": (DEMERGER, "CHILD"),
}

HELD_MESSAGE = (
    "Held for review (not imported): {type} is a corporate action, not a transaction. "
    "Nothing was created for this row. Review it with: python manage.py review_restructurings"
)

MONEY = Decimal("0.0001")
TOLERANCE = Decimal("0.01")


class RestructuringError(Exception):
    """A restructuring cannot be approved as asked (shown to the user)."""


def kind_of(transaction_type):
    """(kind, role) for a merger/demerger upload type, else None."""
    if not transaction_type:
        return None
    key = re.sub(r"\s+", " ", str(transaction_type).strip().upper()).replace("(", " (").replace("  ", " ")
    return _TYPES.get(key)


def _money(value):
    return Decimal(value).quantize(MONEY, rounding=ROUND_HALF_UP)


# ---------------------------------------------------------------- holding ---

def hold_rows(held, *, family, owner, upload=None):
    """Store held upload rows as PendingRestructuring items. Idempotent.

    ``held`` is a list of (excel_row_number, parsed_row). Returns the number of
    new items. Never raises: a failure here must not break the import.
    """
    from investments.models import PendingRestructuring, Transaction

    groups = defaultdict(list)
    for row_number, parsed in held:
        found = kind_of(parsed["transaction_type"])
        if found is None:
            continue
        kind, role = found
        base = (kind, parsed["family_name"], parsed["asset_name"], parsed["transaction_date"])
        # Mergers pair up by equal amount; demergers by PMS and date.
        key = base + ((str(_money(parsed["amount"])),) if kind == MERGER else ())
        groups[key].append({
            "row": row_number,
            "role": role,
            "underlying": parsed["underlying"],
            "isin": parsed["isin"],
            "asset_class": parsed["asset_class"],
            "sub_class": parsed["sub_class"],
            "advisors": parsed["advisors"],
            "portfolio": parsed.get("portfolio") or parsed["asset_name"],
            "quantity": str(parsed["quantity"]),
            "price": str(parsed["price"]),
            "amount": str(parsed["amount"]),
        })

    created = 0
    for key, legs in groups.items():
        kind, family_name, asset_name, event_date = key[:4]
        legs.sort(key=lambda leg: leg["row"])
        material = "|".join(
            [str(family.id), kind, str(event_date), family_name, asset_name]
            + [f"{leg['role']}:{leg['underlying']}:{leg['quantity']}:{leg['amount']}" for leg in legs]
        )
        try:
            with db_transaction.atomic():
                _, was_created = PendingRestructuring.objects.get_or_create(
                    family=family,
                    source_key=hashlib.sha256(material.encode("utf-8")).hexdigest(),
                    defaults={
                        "owner": owner, "upload": upload, "kind": kind, "event_date": event_date,
                        "family_name": family_name, "asset_name": asset_name, "legs": legs,
                    },
                )
            created += int(was_created)
        except Exception:  # noqa: BLE001 - never break the import
            logger.exception("Could not hold %s %s for review", kind, event_date)
    if created:
        logger.warning(
            "Held %s merger/demerger event(s) for review; run: python manage.py review_restructurings", created
        )
    return created


# --------------------------------------------------------------- proposal ---

def _legs(pending, *roles):
    return [leg for leg in pending.legs if leg["role"] in roles]


def find_asset(pending, leg):
    """The existing Asset this leg refers to, or None. Read-only."""
    from investments.models import Asset, Transaction

    ids = list(
        Transaction.objects.filter(
            family=pending.family, family_name=pending.family_name, asset_name=pending.asset_name,
            underlying__iexact=leg["underlying"],
        ).values_list("asset_id", flat=True).distinct()
    )
    if len(ids) == 1:
        return Asset.objects.filter(pk=ids[0]).first()
    if leg.get("isin"):
        found = Asset.objects.filter(family=pending.family, isin__iexact=leg["isin"]).first()
        if found:
            return found
    matches = list(Asset.objects.filter(family=pending.family, name__iexact=leg["underlying"])[:2])
    return matches[0] if len(matches) == 1 else None


def holding_of(pending, asset):
    """What the old stock holds for this PMS today, from the existing engine.

    Returns dict(quantity, cost) or None when it cannot be derived safely: the
    asset is also held outside this PMS/family member, or it has activity after
    the event date.
    """
    from investments.models import Transaction
    from portfolio.services.holding_engine import HoldingCalculationEngine

    rows = Transaction.objects.filter(asset=asset)
    mine = rows.filter(family=pending.family, family_name=pending.family_name, asset_name=pending.asset_name)
    if rows.exclude(pk__in=mine.values("pk")).exists() or mine.filter(transaction_date__gt=pending.event_date).exists():
        return None
    position = HoldingCalculationEngine.calculate_position(asset)
    return {"quantity": position["quantity"], "cost": position["invested_value"]}


def build_proposal(pending):
    """Read-only description of what approval would do."""
    problems = []
    if pending.status != "PENDING":
        problems.append(f"already {pending.status.lower()}")
    if pending.kind == MERGER:
        old, new = _legs(pending, "FROM"), _legs(pending, "TO")
        if len(old) != 1 or len(new) != 1:
            problems.append("needs exactly one *Merged and one *Merger row (review manually)")
        source = old[0] if len(old) == 1 else None
    else:
        parents, children = _legs(pending, "PARENT"), _legs(pending, "CHILD")
        if len(parents) != 1 or not children:
            problems.append("needs one *DeMerger row and at least one *DeMerger (New) row (review manually)")
        source = parents[0] if len(parents) == 1 else None
    asset, holding = None, None
    if source is not None:
        asset = find_asset(pending, source)
        if asset is None:
            problems.append(f"no existing holding found for {source['underlying']}")
        else:
            holding = holding_of(pending, asset)
    return {"source": source, "asset": asset, "holding": holding, "problems": problems}


# ------------------------------------------------------------------ apply ---

def parse_split(text):
    """'Name=0.10,Other=0.20' -> {'name': Decimal('0.10'), 'other': Decimal('0.20')}"""
    result = {}
    for part in str(text or "").split(","):
        if not part.strip():
            continue
        name, separator, value = part.rpartition("=")
        if not separator or not name.strip():
            raise RestructuringError(f"Bad --split entry {part.strip()!r}; use 'Company name=0.10'.")
        try:
            result[name.strip().lower()] = Decimal(value.strip())
        except InvalidOperation as exc:
            raise RestructuringError(f"Bad fraction {value.strip()!r} in --split.") from exc
    return result


def _make_transaction(pending, leg, asset, kind, quantity, amount, index, note):
    from investments.models import Transaction

    quantity = Decimal(quantity)
    amount = _money(amount)
    price = (amount / quantity).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP) if quantity else Decimal("0")
    return Transaction.objects.create(
        owner=pending.owner, family=pending.family, family_name=pending.family_name,
        portfolio=leg["portfolio"], asset_class=leg["asset_class"], sub_class=leg["sub_class"],
        asset_name=pending.asset_name, underlying=leg["underlying"], advisors=leg["advisors"],
        asset=asset, transaction_type=kind, transaction_date=pending.event_date,
        quantity=quantity, price_per_unit=price, amount=amount, fees=Decimal("0"),
        notes=f"{note} (approved review #{pending.id})", source="OTHER",
        source_key=hashlib.sha256(f"restructuring:{pending.id}:{index}".encode()).hexdigest(),
    )


def _target_asset(pending, leg):
    """Existing asset for a new-stock leg, else created exactly like an import would."""
    from investments.services.transaction_import import TransactionImporter

    found = find_asset(pending, leg)
    if found is not None:
        return found
    return TransactionImporter._get_or_create_asset(
        owner=pending.owner, family=pending.family,
        asset_name=TransactionImporter.resolve_security_identity_name(
            asset_name=pending.asset_name, underlying=leg["underlying"]),
        isin=leg.get("isin") or "", asset_class=leg["asset_class"], sub_class=leg["sub_class"],
        allow_name_match=True,
    )


@db_transaction.atomic
def apply(pending_id, *, user, cost, split=None, force=False):
    """Create the transactions for an approved restructuring. Returns their ids."""
    from investments.models import PendingRestructuring
    from portfolio.services.portfolio_position_engine import PortfolioPositionEngine

    pending = PendingRestructuring.objects.select_for_update(of=("self",)).select_related("family", "owner").get(pk=pending_id)
    proposal = build_proposal(pending)
    if proposal["problems"]:
        raise RestructuringError("Cannot approve #%s: %s." % (pending.id, "; ".join(proposal["problems"])))
    source, asset, holding = proposal["source"], proposal["asset"], proposal["holding"]

    cost = _money(cost)
    if cost <= 0:
        raise RestructuringError("--cost must be greater than zero.")
    quantity = Decimal(source["quantity"])
    if holding is not None:
        if abs(holding["cost"] - cost) > TOLERANCE and not force:
            raise RestructuringError(
                f"--cost {cost} does not match the cost this holding carries today ({_money(holding['cost'])}). "
                "Use that figure, or add --force if you have checked it."
            )
        if abs(holding["quantity"] - quantity) > Decimal("0.0001") and not force:
            raise RestructuringError(
                f"The upload says {quantity} units were merged/demerged but the holding has {holding['quantity']}; "
                "the quantity differs. Fix the data, or add --force if you have checked it."
            )

    created = []
    if pending.kind == MERGER:
        target = _legs(pending, "TO")[0]
        name_from, name_to = source["underlying"], target["underlying"]
        created.append(_make_transaction(pending, source, asset, "SELL", quantity, cost, 0,
                                         f"Merger: cost carried to {name_to}"))
        created.append(_make_transaction(pending, target, _target_asset(pending, target), "BUY",
                                         target["quantity"], cost, 1, f"Merger: cost carried from {name_from}"))
    else:
        children = _legs(pending, "CHILD")
        split = split or {}
        wanted = {leg["underlying"].strip().lower() for leg in children}
        if not split:
            raise RestructuringError(
                "A demerger needs --split with the share of the cost that moves to each new company, "
                "for example --split \"Company A=0.10,Company B=0.20\" (from the company's cost-allocation notice)."
            )
        if set(split) != wanted:
            raise RestructuringError(
                "--split must name exactly: " + ", ".join(sorted(leg["underlying"] for leg in children)) + "."
            )
        if any(value <= 0 or value >= 1 for value in split.values()) or sum(split.values()) >= 1:
            raise RestructuringError("Each fraction must be between 0 and 1, and together they must be below 1.")
        child_amounts = {leg["underlying"].strip().lower(): _money(cost * split[leg["underlying"].strip().lower()])
                         for leg in children}
        parent_cost = cost - sum(child_amounts.values())
        created.append(_make_transaction(pending, source, asset, "SELL", quantity, cost, 0,
                                         "Demerger: old cost removed"))
        created.append(_make_transaction(pending, source, asset, "BUY", quantity, parent_cost, 1,
                                         "Demerger: cost after the demerger"))
        for index, leg in enumerate(children, start=2):
            created.append(_make_transaction(
                pending, leg, _target_asset(pending, leg), "BUY", leg["quantity"],
                child_amounts[leg["underlying"].strip().lower()], index,
                f"Demerger: cost received from {source['underlying']}",
            ))

    pending.status = "APPLIED"
    pending.applied_transaction_ids = [item.id for item in created]
    pending.reviewed_by = user
    pending.reviewed_at = timezone.now()
    pending.save(update_fields=["status", "applied_transaction_ids", "reviewed_by", "reviewed_at"])
    if pending.owner is not None:
        PortfolioPositionEngine.rebuild_all_for_user(pending.owner)
    return pending.applied_transaction_ids


def reject(pending_id, *, user):
    from investments.models import PendingRestructuring

    pending = PendingRestructuring.objects.get(pk=pending_id)
    if pending.status != "PENDING":
        raise RestructuringError(f"#{pending.id} is already {pending.status.lower()}.")
    pending.status = "REJECTED"
    pending.reviewed_by = user
    pending.reviewed_at = timezone.now()
    pending.save(update_fields=["status", "reviewed_by", "reviewed_at"])
