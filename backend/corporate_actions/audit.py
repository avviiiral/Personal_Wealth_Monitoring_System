"""Read-only corporate-action reconciliation audit helpers."""
import re
from decimal import Decimal, InvalidOperation

from django.db.models import Max
from investments.models import Asset, AssetCategory, AssetUnderlyingHolding
from mutual_funds.underlying_models import MutualFundUnderlying
from mutual_funds.models import MutualFundHolding


def _decimal(value):
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def extract_dividend_amount(purpose):
    """Extract an explicit rupee-per-share amount; never infer an amount."""
    text = str(purpose or "")
    currency = r"(?:₹\s*|INR\s*|Rs\.?\s*|Re\.?\s*|Rupees?\s*)"
    per_share = r"(?:per\s+(?:equity\s+)?sh(?:are)?|each\s+(?:equity\s+)?sh(?:are)?)"
    patterns = (
        currency + r"([0-9]+(?:,[0-9]{3})*(?:\.[0-9]+)?)\s*(?:/-)?\s*" + per_share,
        r"(?:dividend\s*(?:of|@|:)?\s*)" + currency + r"([0-9]+(?:,[0-9]{3})*(?:\.[0-9]+)?)",
    )
    values = []
    for pattern in patterns:
        values.extend(_decimal(match.replace(",", "")) for match in re.findall(pattern, text, re.I))
    unique = {value for value in values if value is not None}
    return next(iter(unique)) if len(unique) == 1 else None


def extract_entitlement_ratio(purpose, action_type):
    """Return old/new units only for an unambiguous explicit ratio."""
    text = str(purpose or "")
    if action_type == "BONUS":
        if re.search(r"\b(?:ncrps|non[- ]?convertible\s+redeemable\s+preference)", text, re.I):
            return None, None
        matches = re.findall(r"\b(?:bonus\s*)?(\d+)\s*[:/]\s*(\d+)\b", text, re.I)
        ratios = {(int(right), int(left)) for left, right in matches if int(left) > 0 and int(right) > 0}
        return next(iter(ratios)) if len(ratios) == 1 else (None, None)
    if action_type in {"STOCK_SPLIT", "REVERSE_SPLIT"}:
        matches = re.findall(
            r"from\s+(?:₹|Rs\.?\s*)([0-9]+(?:\.[0-9]+)?)\s*(?:/-)?\s*(?:per\s+share\s*)?(?:to|into)\s+(?:₹|Rs\.?\s*)([0-9]+(?:\.[0-9]+)?)",
            text, re.I,
        )
        ratios = set()
        for old_face, new_face in matches:
            old_value, new_value = _decimal(old_face), _decimal(new_face)
            if old_value and new_value and old_value > 0 and new_value > 0:
                factor = old_value / new_value
                if factor == factor.to_integral_value():
                    ratios.add((Decimal("1"), factor))
        return next(iter(ratios)) if len(ratios) == 1 else (None, None)
    return None, None


def _row(name, identifier, family_id=None, quantity=None, weight=None, snapshot_date=None):
    return {
        "investment_name": name, "investment_id": identifier, "family_id": family_id,
        "quantity": str(quantity) if quantity is not None else None,
        "holding_weight": str(weight) if weight is not None else None,
        "snapshot_date": snapshot_date.isoformat() if snapshot_date else None,
    }


def inspect_isin_exposures(isin):
    """Inspect held direct/MF/PMS positions by exact ISIN only."""
    result = {"direct_equity": [], "mutual_fund": [], "pms": []}
    errors = []
    normalized = str(isin or "").strip().upper()
    if not normalized:
        return result, ["ISIN unavailable; exact-ISIN reconciliation cannot be attempted."]
    try:
        rows = Asset.objects.filter(
            is_active=True, category=AssetCategory.STOCK, isin__iexact=normalized,
            holding__quantity__gt=0,
        ).select_related("holding")
        result["direct_equity"] = [
            _row(asset.name, asset.id, asset.family_id or asset.holding.family_id, asset.holding.quantity)
            for asset in rows
        ]
    except Exception as exc:
        errors.append("direct_equity query failed: " + str(exc))
    try:
        rows = MutualFundUnderlying.objects.filter(isin__iexact=normalized)
        scheme_ids = rows.values_list("scheme_id", flat=True).distinct()
        # A scheme's latest portfolio date must be computed across its entire
        # disclosed portfolio, not only rows that still contain this ISIN.
        latest = MutualFundUnderlying.objects.filter(
            scheme_id__in=scheme_ids
        ).values("scheme_id").annotate(latest_date=Max("portfolio_date"))
        latest_dates = {item["scheme_id"]: item["latest_date"] for item in latest}
        current = rows.filter(scheme__is_active=True, scheme__holding__units__gt=0).select_related("scheme", "scheme__holding")
        for underlying in current.order_by("scheme_id", "-portfolio_date"):
            if latest_dates.get(underlying.scheme_id) != underlying.portfolio_date:
                continue
            result["mutual_fund"].append(_row(
                underlying.scheme.scheme_name, underlying.scheme_id,
                underlying.scheme.family_id or underlying.scheme.holding.family_id,
                underlying.quantity, underlying.percentage_of_nav, underlying.portfolio_date,
            ))
    except Exception as exc:
        errors.append("mutual_fund query failed: " + str(exc))
    try:
        rows = AssetUnderlyingHolding.objects.filter(
            isin__iexact=normalized, asset__is_active=True, asset__holding__quantity__gt=0,
        ).select_related("asset", "asset__holding")
        result["pms"] = [
            _row(row.asset.name, row.asset_id, row.family_id, row.asset.holding.quantity, row.holding_percentage)
            for row in rows
        ]
    except Exception as exc:
        errors.append("pms query failed: " + str(exc))
    return result, errors


def build_event_audit(event, source, source_url, metadata):
    action_type = event.get("action_type")
    source_isin = str(event.get("source_isin") or "").strip().upper()
    resolved_isin = str(event.get("resolved_isin") or event.get("isin") or "").strip().upper()
    source_isin_required = bool(event.get("source_isin_required"))
    isin = source_isin or ("" if source_isin_required else resolved_isin)
    isin_mismatch = bool(source_isin and resolved_isin and source_isin != resolved_isin)
    exposures, errors = inspect_isin_exposures(isin)
    purpose = event.get("purpose") or metadata.get("purpose") or ""
    dividend = _decimal(event.get("dividend_per_share"))
    if dividend is None and action_type == "CASH_DIVIDEND":
        dividend = extract_dividend_amount(purpose)
    old_units, new_units = extract_entitlement_ratio(purpose, action_type)
    reasons = []
    if not action_type:
        reasons.append("Corporate-action purpose is ambiguous or unsupported.")
    if isin_mismatch:
        reasons.append(f"Source ISIN {source_isin} differs from portfolio ISIN {resolved_isin}; manual identity review is required.")
    if action_type == "BONUS" and re.search(r"\b(?:ncrps|non[- ]?convertible\s+redeemable\s+preference)", purpose, re.I):
        reasons.append("NCRPS/preference-share entitlement requires instrument-class verification; ordinary equity adjustment is blocked.")
    if not event.get("ex_date"):
        reasons.append("Ex-date is missing.")
    if event.get("status") in {"CANCELLED", "CANCELED", "DELETED"}:
        reasons.append("Event is cancelled or deleted.")
    if action_type in {"BONUS", "STOCK_SPLIT", "REVERSE_SPLIT"} and (old_units is None or new_units is None):
        reasons.append("Entitlement ratio is not reliably available.")
    if action_type == "CASH_DIVIDEND" and dividend is None:
        reasons.append("Dividend per-share amount is not explicitly available.")
    has_exposure = any(exposures[key] for key in exposures)
    identity_match_required = bool(not isin and (event.get("symbol") or event.get("company_name")))
    if identity_match_required and not event.get("symbol"):
        reasons.append("Source ISIN and symbol are unavailable; exact full-name candidate review may be possible.")

    if identity_match_required:
        reasons.append("Source ISIN unavailable; any symbol/name candidate requires explicit user approval.")
    reconciliation = (
        "RECONCILIATION_FAILED" if errors else
        "ISIN_MISMATCH" if isin_mismatch else
        "SOURCE_ISIN_MISSING" if not isin else
        "MATCHED_EXPOSURE" if has_exposure else
        "NO_PORTFOLIO_EXPOSURE"
    )
    return {
        "company_name": event.get("company_name") or "", "nse_symbol": event.get("symbol") or "",
        "isin": isin or None, "source_isin": source_isin or None,
        "resolved_isin": resolved_isin or None, "isin_mismatch": isin_mismatch,
        "exchange": event.get("exchange") or "", "source": source,
        "source_url": source_url or "", "action_type": action_type, "original_nse_purpose": purpose,
        "ex_date": event.get("ex_date").isoformat() if event.get("ex_date") else None,
        "record_date": event.get("record_date").isoformat() if event.get("record_date") else None,
        "payment_date": event.get("payment_date").isoformat() if event.get("payment_date") else None,
        "dividend_per_share": str(dividend) if dividend is not None else None,
        "old_units": str(old_units) if old_units is not None else None,
        "new_units": str(new_units) if new_units is not None else None,
        "exposures": exposures, "exposure_counts": {key: len(value) for key, value in exposures.items()},
        "classification_status": "CLASSIFIED" if action_type else "MANUAL_REVIEW",
        "validation_status": "VALID" if not reasons else "MANUAL_REVIEW",
        "identity_match_required": identity_match_required,
        "reconciliation_status": reconciliation, "reconciliation_errors": errors,
        "review_reasons": reasons, "skip_reason": "; ".join(reasons) if reasons else None,
        "duplicate_of": None, "source_metadata": metadata,
    }


def mark_duplicate_events(events):
    """Flag later occurrences of (ISIN, action type, ex-date), retaining every row."""
    seen = {}
    for index, event in enumerate(events):
        # Stable schema: every event has a duplicate marker, including unique rows.
        event.setdefault("duplicate_of", None)
        key = (event.get("isin"), event.get("action_type"), event.get("ex_date"))
        if all(key):
            if key in seen:
                event["duplicate_of"] = seen[key]
                event["validation_status"] = "MANUAL_REVIEW"
                event["review_reasons"].append("Potential duplicate event for the same ISIN, action type and ex-date.")
                event["skip_reason"] = "; ".join(event["review_reasons"])
            else:
                seen[key] = index
