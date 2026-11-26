"""Read merger, demerger and buyback terms from exchange announcement text.

NSE's corporate-actions feed usually reports a merger as "Amalgamation" or a
demerger as "Demerger", with no ratio, no resulting company and no cost split.
Those details are in the company's announcements (``Filing`` rows) and
sometimes in the feed's purpose text. This module extracts only terms that are
stated explicitly:

* the share-exchange ratio, written as "N shares of <Company> for every M
  shares";
* the resulting / transferee company, named in that sentence or defined as
  "<Company> Limited ("Resulting Company")";
* the demerger cost split, when a sentence about cost states the percentage for
  the resulting company and for the original company and they add up to 100;
* the buyback or tender-offer price per share, and whether a buyback is an
  open-market buyback (no tender, so there is nothing to reconcile).

A term is used only when every announcement that states it agrees; otherwise
it stays blank and the event asks for it once. A resulting company becomes an
ISIN only through an exact match of its normalised legal name in the security
master or an Asset record, never a fuzzy match. ``fill_terms_from_announcements``
fills blank fields only, records where each value came from in ``metadata``,
and never touches an event that is already booked. Nothing here changes a
holding: quantities still change only when a family member accepts.
"""
import logging
import re
from datetime import datetime, time, timedelta
from decimal import Decimal, InvalidOperation

from django.db import transaction as db_transaction
from django.db.models import Q
from django.utils import timezone

from .models import CorporateAction, CorporateActionReconciliationStatus, CorporateActionType

logger = logging.getLogger(__name__)

RESTRUCTURING = {CorporateActionType.MERGER, CorporateActionType.DEMERGER}
TENDER_TYPES = {CorporateActionType.BUYBACK, CorporateActionType.TENDER_OFFER}
SUPPORTED = RESTRUCTURING | TENDER_TYPES
BOOKED = {CorporateActionReconciliationStatus.COMPLETED, CorporateActionReconciliationStatus.APPROVED}

# Announcements published this long before / after the ex-date are read.
LOOKBACK_DAYS = 400
LOOKAHEAD_DAYS = 45
MAX_FILINGS = 200

FILING_KEYWORDS = {
    CorporateActionType.MERGER: ("amalgamation", "merger"),
    CorporateActionType.DEMERGER: ("demerger", "spin-off", "spin off", "arrangement"),
    CorporateActionType.BUYBACK: ("buyback", "buy-back", "buy back"),
    CorporateActionType.TENDER_OFFER: ("tender offer", "open offer"),
}

_NUMBER = r"(\d+(?:\.\d+)?)"
_SPELLED = r"(?:\s*\([A-Za-z\s-]+\))?"              # "42 (Forty Two)"
_SHARE = r"(?:new\s+|fully\s+paid[\s-]*up\s+|equity\s+)*shares?"
RATIO_SENTENCE = re.compile(
    rf"{_NUMBER}{_SPELLED}\s+{_SHARE}\b(?P<mid>.{{0,200}}?)\bfor\s+every\s+{_NUMBER}{_SPELLED}\s+{_SHARE}\b",
    re.IGNORECASE | re.DOTALL,
)
LEGAL_END = re.compile(r"\b(?:Limited|Ltd\b\.?)")
DEFINED_RESULT = re.compile(
    r"\(\s*(?:hereinafter\s+(?:referred\s+to\s+as\s+|called\s+)?)?[\"“'‘]?\s*(?:the\s+)?"
    r"(?:Resulting|Transferee|Amalgamated)\s+Company",
)
PERCENT_TO = re.compile(
    r"(\d{1,3}(?:\.\d+)?)\s*%\s*(?:to|for|towards)\s+(?:the\s+)?(?:equity\s+shares\s+of\s+)?"
    r"(?P<who>[A-Za-z][A-Za-z0-9&.'\- ]{0,80}?)(?=\s+(?:and|&)\b|\s*[,;)]|\.(?:\s|$)|$)",
    re.IGNORECASE,
)
PRICE = re.compile(
    r"(?:buy[\s-]?back(?:\s+offer)?|offer|tender)\s+price\s+(?:of\s+|at\s+|is\s+|:\s*)?(?:Rs\.?|INR|₹)\s*([\d,]+(?:\.\d+)?)"
    r"|at\s+(?:a\s+)?price\s+of\s+(?:Rs\.?|INR|₹)\s*([\d,]+(?:\.\d+)?)\s*(?:/-)?\s*(?:\([^)]*\)\s*)?(?:only\s+)?"
    r"per\s+(?:fully\s+paid[\s-]*up\s+)?(?:equity\s+)?share",
    re.IGNORECASE,
)
OPEN_MARKET = re.compile(r"\bopen\s+market\b|\bstock\s+exchange\s+route\b", re.IGNORECASE)
TENDER_ROUTE = re.compile(r"\btender\s+offer\b|\btender\s+route\b|\bproportionate\s+basis\b", re.IGNORECASE)
COST_WORDS = re.compile(r"\bcost\b", re.IGNORECASE)
SOURCE_WORDS = ("demerged company", "transferor company", "amalgamating company")
RESULT_WORDS = ("resulting company", "transferee company", "amalgamated company")
LEGAL_SUFFIX = re.compile(r"\b(?:limited|ltd|pvt|private)\b")
NAME_CONNECTORS = {"of", "and", "&", "the"}
SPLIT_TOLERANCE = Decimal("0.05")


def normalise_company(name):
    """Casefolded legal name without punctuation or Limited/Ltd suffixes."""
    text = re.sub(r"[^a-z0-9& ]+", " ", str(name or "").casefold())
    text = LEGAL_SUFFIX.sub(" ", text)
    return " ".join(text.split())


def _initials(name):
    """Initials of a company name, with and without the legal suffix ("RIL", "RI")."""
    words = re.sub(r"[^a-z0-9& ]+", " ", str(name or "").casefold()).split()
    with_suffix = "".join(word[0] for word in words if word not in NAME_CONNECTORS)
    without = "".join(word[0] for word in words if word not in NAME_CONNECTORS and not LEGAL_SUFFIX.fullmatch(word))
    return {value for value in (with_suffix, without) if len(value) >= 2}


def _decimal(value):
    """Decimal from text such as "4,150", without a trailing exponent; None if not a number."""
    try:
        result = Decimal(str(value).replace(",", ""))
    except (InvalidOperation, TypeError, ValueError):
        return None
    if not result.is_finite():
        return None
    return result.quantize(Decimal(1)) if result == result.to_integral_value() else result.normalize()


def _name_ending_at(text, end):
    """The company name whose legal suffix ends at ``end`` ("HDFC Bank Limited")."""
    tokens = text[:end].split()
    if not tokens:
        return ""
    name = [tokens.pop()]                      # the Limited / Ltd token
    while tokens:
        token = tokens[-1]
        bare = token.strip("\"“”'‘’(),")
        if not bare or LEGAL_END.fullmatch(bare) or bare.endswith(",") or token.endswith(","):
            break
        if bare.lower() in NAME_CONNECTORS:
            before = tokens[-2].strip("\"“”'‘’(") if len(tokens) > 1 else ""
            if not before or not (before[0].isupper() or before[0].isdigit()) or LEGAL_END.fullmatch(before):
                break
            name.append(tokens.pop())
            continue
        if not (bare[0].isupper() or bare[0].isdigit()):
            break
        name.append(tokens.pop())
    while name and name[-1].strip("\"“”'‘’(").lower() in NAME_CONNECTORS:
        name.pop()
    result = " ".join(reversed(name)).strip(" \"“”'‘’(,;")
    return result[:255] if re.search(r"[A-Za-z]", result.split()[0] if result else "") else ""


def _last_company(segment):
    """The last company name (ending in Limited/Ltd) in ``segment``, or ""."""
    ends = list(LEGAL_END.finditer(segment))
    return _name_ending_at(segment, ends[-1].end()) if ends else ""


def _sentences(text):
    """Split announcement text into sentences."""
    return [part for part in re.split(r"(?<=[.;])\s+(?=[A-Z0-9(])|\n+", text) if part.strip()]


def extract_terms(text, action_type, *, source_name="", source_symbol=""):
    """Terms stated in one announcement. Values found more than once must agree.

    Returns a dict that may contain old_units, new_units, resulting_security_name,
    cost_allocation_ratio, cash_per_unit and buyback_method, plus ``ambiguous``
    (a dict of term -> the conflicting values) when one text disagrees with itself.
    """
    text = " ".join(str(text or "").split())
    found, ambiguous = {}, {}
    if not text:
        return found
    if action_type in RESTRUCTURING:
        source_key = normalise_company(source_name)
        ratios, names = {}, {}
        for match in RATIO_SENTENCE.finditer(text):
            new_units, old_units = _decimal(match.group(1)), _decimal(match.group(3))
            if not new_units or not old_units or new_units <= 0 or old_units <= 0:
                continue
            # 1 for every 10 and 2 for every 20 are the same entitlement.
            ratios.setdefault(new_units / old_units, (old_units, new_units))
            name = _last_company(match.group("mid"))
            if normalise_company(name) and normalise_company(name) != source_key:
                names[normalise_company(name)] = name
        for match in DEFINED_RESULT.finditer(text):
            head = text[:match.start()].rstrip()
            name = _name_ending_at(head, len(head)) if LEGAL_END.search(head[-12:]) else ""
            if normalise_company(name) and normalise_company(name) != source_key:
                names[normalise_company(name)] = name
        if len(ratios) == 1:
            found["old_units"], found["new_units"] = next(iter(ratios.values()))
        elif ratios:
            ambiguous["ratio"] = sorted(f"{old}:{new}" for old, new in ratios.values())
        if len(names) == 1:
            found["resulting_security_name"] = next(iter(names.values()))
        elif names:
            ambiguous["resulting_security_name"] = sorted(names.values())
        if action_type == CorporateActionType.DEMERGER:
            split = _cost_split(text, source_name, source_symbol, found.get("resulting_security_name", ""))
            if split == "ambiguous":
                ambiguous["cost_allocation_ratio"] = True
            elif split is not None:
                found["cost_allocation_ratio"] = split
    if action_type in TENDER_TYPES:
        prices = {_decimal(a or b) for a, b in PRICE.findall(text)} - {None}
        prices = {price for price in prices if price > 0}
        if len(prices) == 1:
            found["cash_per_unit"] = next(iter(prices))
        elif prices:
            ambiguous["cash_per_unit"] = sorted(str(price) for price in prices)
        if action_type == CorporateActionType.BUYBACK:
            tender = bool(TENDER_ROUTE.search(text))
            if OPEN_MARKET.search(text) and not tender:
                found["buyback_method"] = "OPEN_MARKET"
            elif tender:
                found["buyback_method"] = "TENDER"
    if ambiguous:
        found["ambiguous"] = ambiguous
    return found


def _mentions_company(who, keys, initials, words):
    """True when the text after a percentage refers to this company (name, symbol, initials or role)."""
    if any(word in who.casefold() for word in words):
        return True
    who_key = normalise_company(who)
    if not who_key:
        return False
    if who_key.replace(" ", "") in initials:
        return True
    return any(key and (who_key == key or who_key.startswith(key + " ")) for key in keys)


def _cost_split(text, source_name, source_symbol, result_name):
    """Fraction of the original cost that moves to the resulting company.

    Returns a Decimal, None when not stated, or "ambiguous" when sentences disagree.
    A sentence counts only when it names both companies' percentages (by name,
    symbol, initials or "Resulting/Demerged Company") and they add up to 100.
    """
    source_keys = {normalise_company(source_name), normalise_company(source_symbol)} - {""}
    source_initials = _initials(source_name)
    result_keys = {normalise_company(result_name)} - {""}
    result_initials = _initials(result_name)
    candidates = set()
    for sentence in _sentences(text):
        if not COST_WORDS.search(sentence):
            continue
        result_pcts, source_pcts = [], []
        for match in PERCENT_TO.finditer(sentence):
            pct, who = _decimal(match.group(1)), match.group("who")
            if pct is None or not 0 < pct < 100:
                continue
            is_result = _mentions_company(who, result_keys, result_initials, RESULT_WORDS)
            is_source = _mentions_company(who, source_keys, source_initials, SOURCE_WORDS)
            if is_result and not is_source:
                result_pcts.append(pct)
            elif is_source and not is_result:
                source_pcts.append(pct)
        if len(result_pcts) == 1 and len(source_pcts) == 1 and abs(result_pcts[0] + source_pcts[0] - 100) <= SPLIT_TOLERANCE:
            candidates.add(result_pcts[0])
    if len(candidates) > 1:
        return "ambiguous"
    if not candidates:
        return None
    return (next(iter(candidates)) / Decimal(100)).quantize(Decimal("0.00000001"))


def resolve_company(name):
    """(isin, symbol, name) for one exact normalised legal-name match, else None."""
    from investments.models import Asset, SecurityMaster

    key = normalise_company(name)
    if not key:
        return None
    first = key.split()[0]
    matches = {}
    masters = (SecurityMaster.objects.filter(asset_name__icontains=first)
               .exclude(isin__isnull=True).exclude(isin="").only("isin", "asset_name")[:500])
    for row in masters:
        if normalise_company(row.asset_name) == key:
            matches.setdefault(row.isin.strip().upper(), {"name": row.asset_name, "symbols": set()})
    assets = (Asset.objects.filter(is_active=True, name__icontains=first)
              .exclude(isin__isnull=True).exclude(isin="").only("isin", "name", "symbol")[:500])
    for asset in assets:
        if normalise_company(asset.name) == key:
            entry = matches.setdefault(asset.isin.strip().upper(), {"name": asset.name, "symbols": set()})
            if asset.symbol and asset.symbol.strip():
                entry["symbols"].add(asset.symbol.strip().upper())
    if len(matches) != 1:
        return None
    isin, entry = next(iter(matches.items()))
    symbol = next(iter(entry["symbols"])) if len(entry["symbols"]) == 1 else ""
    return isin, symbol, entry["name"]


def _announcement_texts(action):
    """(label, text) pairs: the provider's purpose text plus nearby filings for the security."""
    from filing_intelligence.models import Filing

    metadata = action.metadata if isinstance(action.metadata, dict) else {}
    texts = []
    if metadata.get("purpose"):
        texts.append(("exchange purpose text", str(metadata["purpose"])))
    identity = Q()
    if action.isin and action.isin.strip():
        identity |= Q(isin__iexact=action.isin.strip())
    if action.symbol and action.symbol.strip():
        identity |= Q(symbol__iexact=action.symbol.strip())
    if not identity:
        return texts
    start = timezone.make_aware(datetime.combine(action.ex_date - timedelta(days=LOOKBACK_DAYS), time.min))
    end = timezone.make_aware(datetime.combine(action.ex_date + timedelta(days=LOOKAHEAD_DAYS), time.min))
    words = Q()
    for word in FILING_KEYWORDS[action.action_type]:
        words |= Q(subject__icontains=word) | Q(details__icontains=word)
    filings = (Filing.objects.filter(identity).filter(words)
               .filter(published_at__gte=start, published_at__lt=end)
               .order_by("published_at", "id")[:MAX_FILINGS])
    for filing in filings:
        texts.append((f"{filing.exchange} announcement #{filing.id} ({filing.published_at.date().isoformat()})",
                      f"{filing.subject}. {filing.details}"))
    return texts


def gather_terms(action):
    """Terms that every announcement for ``action`` agrees on.

    Returns (values, sources, ambiguous): the agreed values, the announcement
    each came from, and the terms announcements disagree on (left blank).
    """
    seen = {}       # term -> {comparison key: (value, label)}
    ambiguous = {}
    for label, text in _announcement_texts(action):
        found = extract_terms(text, action.action_type, source_name=action.security_name, source_symbol=action.symbol)
        for term, value in (found.pop("ambiguous", None) or {}).items():
            ambiguous[term] = value
        if "old_units" in found and "new_units" in found:
            found["ratio"] = (found.pop("old_units"), found.pop("new_units"))
        for term, value in found.items():
            if term == "resulting_security_name":
                key = normalise_company(value)
            elif term == "ratio":
                key = value[1] / value[0]
            else:
                key = value
            seen.setdefault(term, {}).setdefault(key, (value, label))
    values, sources = {}, {}
    for term, options in seen.items():
        if term in ambiguous:
            continue
        if len(options) > 1:
            ambiguous[term] = sorted(f"{v[0][0]}:{v[0][1]}" if term == "ratio" else str(v[0]) for v in options.values())
            continue
        value, label = next(iter(options.values()))
        if term == "ratio":
            values["old_units"], values["new_units"] = value
            sources["old_units"] = sources["new_units"] = label
        else:
            values[term] = value
            sources[term] = label
    return values, sources, ambiguous


FILLABLE_FIELDS = ("old_units", "new_units", "cash_per_unit", "cost_allocation_ratio",
                   "resulting_security_name", "resulting_isin", "resulting_symbol")


def fill_terms_from_announcements(action):
    """Fill blank merger/demerger/buyback terms from announcements. Returns the filled field names.

    Every read and write runs in its own savepoint: if anything fails, the
    error is logged, the event is left exactly as it was, and the caller's
    reconciliation carries on with the stored terms.
    """
    if action.action_type not in SUPPORTED or action.ex_date is None or action.pk is None:
        return []
    try:
        with db_transaction.atomic():
            return _fill_terms(action)
    except Exception:  # noqa: BLE001 - reading announcements must never block a reconciliation
        logger.exception("Could not fill announcement terms for corporate action %s", action.id)
        action.refresh_from_db()
        return []


def _fill_terms(action):
    """Body of fill_terms_from_announcements; runs inside its savepoint."""
    if action.reconciliations.filter(status__in=BOOKED).exists():
        return []
    values, sources, ambiguous = gather_terms(action)
    name = values.get("resulting_security_name") or action.resulting_security_name
    if name and not action.resulting_isin:
        resolved = resolve_company(name)
        if resolved is not None:
            values["resulting_isin"], symbol, _ = resolved
            sources["resulting_isin"] = "security master (exact company name)"
            if symbol:
                values["resulting_symbol"] = symbol
                sources["resulting_symbol"] = sources["resulting_isin"]
    filled = []
    ratio_blank = not action.old_units and not action.new_units
    for field_name in FILLABLE_FIELDS:
        if field_name not in values or getattr(action, field_name) not in (None, ""):
            continue
        if field_name in ("old_units", "new_units") and not ratio_blank:
            continue  # never combine half a stored ratio with half an extracted one
        setattr(action, field_name, values[field_name])
        filled.append(field_name)
    metadata = dict(action.metadata) if isinstance(action.metadata, dict) else {}
    previous = metadata.get("announcement_terms") or {}
    record = {
        "filled": {**(previous.get("filled") or {}),
                   **{field_name: {"value": str(getattr(action, field_name)), "source": sources.get(field_name, "")}
                      for field_name in filled}},
        "ambiguous": {term: value for term, value in ambiguous.items() if value is not True},
        "buyback_method": values.get("buyback_method") or previous.get("buyback_method") or "",
    }
    changed = list(filled)
    if record != previous:
        metadata["announcement_terms"] = record
        action.metadata = metadata
        changed.append("metadata")
    if changed:
        action.save(update_fields=sorted(set(changed)) + ["updated_at"])
    return filled


def buyback_method(action):
    """"TENDER", "OPEN_MARKET" or "" as read from the announcements."""
    metadata = action.metadata if isinstance(action.metadata, dict) else {}
    return str((metadata.get("announcement_terms") or {}).get("buyback_method") or "")


def missing_terms(action):
    """Plain names of the terms still needed before quantities can be calculated."""
    kind = action.action_type
    missing = []
    if kind in RESTRUCTURING:
        if not action.old_units or not action.new_units:
            missing.append("share ratio")
        if not (action.resulting_isin or "").strip():
            missing.append("new company ISIN")
        if kind == CorporateActionType.DEMERGER and action.cost_allocation_ratio is None:
            missing.append("cost split (%)")
    elif kind in TENDER_TYPES:
        if buyback_method(action) != "OPEN_MARKET" and not action.cash_per_unit:
            missing.append("buyback price per share" if kind == CorporateActionType.BUYBACK else "offer price per share")
    elif kind in (CorporateActionType.BONUS, CorporateActionType.STOCK_DIVIDEND,
                  CorporateActionType.STOCK_SPLIT, CorporateActionType.REVERSE_SPLIT):
        if not action.old_units or not action.new_units:
            missing.append("ratio")
    elif kind == CorporateActionType.CASH_DIVIDEND and not action.cash_per_unit:
        missing.append("dividend per share")
    return missing


SHARED_TERM_FIELDS = (
    "old_units", "new_units", "cash_per_unit", "cost_allocation_ratio", "cash_in_lieu_price",
    "resulting_isin", "resulting_symbol", "resulting_security_name", "record_date", "payment_date",
)


def share_terms_with_family(action, editor, field_names):
    """Copy terms ``editor`` entered to the same event's copies owned by their family members.

    Only blank fields are filled, only for owners who share a family with
    ``editor``, and never on a copy that is already booked, so one person
    entering the terms is enough for the whole family. Returns the copies
    that changed, so their proposals can be recalculated.
    """
    from users.models import FamilyMembership

    names = [name for name in field_names if name in SHARED_TERM_FIELDS]
    if not names:
        return []
    family_ids = list(FamilyMembership.objects.filter(profile__user=editor).values_list("family_group_id", flat=True))
    member_ids = set(FamilyMembership.objects.filter(family_group_id__in=family_ids)
                     .values_list("profile__user_id", flat=True)) - {editor.id, None}
    if not member_ids:
        return []
    lookup = {"action_type": action.action_type, "ex_date": action.ex_date}
    if action.isin:
        lookup["isin__iexact"] = action.isin
    else:
        lookup.update({"isin": "", "symbol__iexact": action.symbol})
    changed_copies = []
    for copy in CorporateAction.objects.filter(owner_id__in=member_ids, **lookup).exclude(id=action.id):
        if copy.reconciliations.filter(status__in=BOOKED).exists():
            continue
        updated = []
        ratio_blank = not copy.old_units and not copy.new_units
        for name in names:
            value = getattr(action, name)
            if value in (None, "") or getattr(copy, name) not in (None, ""):
                continue
            if name in ("old_units", "new_units") and not ratio_blank:
                continue
            setattr(copy, name, value)
            updated.append(name)
        if updated:
            metadata = dict(copy.metadata) if isinstance(copy.metadata, dict) else {}
            metadata["terms_shared_by"] = {"user": editor.get_username(), "fields": sorted(updated),
                                          "at": timezone.now().isoformat(timespec="seconds")}
            copy.metadata = metadata
            copy.save(update_fields=sorted(updated) + ["metadata", "updated_at"])
            changed_copies.append(copy)
    return changed_copies
