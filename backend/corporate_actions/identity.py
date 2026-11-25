"""Safe identity matching for NSE corporate-action events.

Used only when ``corporate_actions.nse_identity_matching`` is true.

1. The provider's own ISIN (NSE returns one per event) is used as is.
2. Otherwise the NSE symbol is mapped to an ISIN only when the Security
   Master (backend/data/security_master.xlsx) or NSE's EQUITY_L.csv gives
   exactly one ISIN, and the two sources do not disagree. Anything else is
   left for the existing explicit-approval flow. Names are never used.

The match method is recorded on CorporateAction.metadata and on
CorporateActionReconciliation.identity_match_method. Nothing here creates or
changes a transaction.
"""
from django.db.models import Q

from config.pwms_config import get as get_pwms_config

SOURCE_ISIN = "SOURCE_ISIN"
SYMBOL_SECURITY_MASTER = "SYMBOL_SECURITY_MASTER"
SYMBOL_EQUITY_L = "SYMBOL_EQUITY_L"


def nse_identity_matching_enabled():
    return bool(get_pwms_config("corporate_actions", "nse_identity_matching", False))


def _bare(symbol):
    value = str(symbol or "").strip().upper()
    return value[:-3] if value.endswith(".NS") else value


def _security_master_isins_for_symbol(symbol):
    from market_data.services.security_resolver import SecurityResolver

    SecurityResolver.reload_if_changed()
    target = f"{_bare(symbol)}{SecurityResolver.NSE_SUFFIX}"
    return {isin for isin, yahoo in SecurityResolver._security_master_isin.items() if yahoo == target}


def _equity_list_isins_for_symbol(symbol):
    from market_data.services.security_resolver import SecurityResolver

    target = _bare(symbol)
    return {
        isin for isin, record in SecurityResolver.nse_equity_list().items()
        if str(record.get("symbol") or "").strip().upper() == target
    }


def resolve_nse_symbol_isin(symbol):
    """Return ``(isin, method)`` or ``(None, "AMBIGUOUS" | "NOT_FOUND")``."""
    if not _bare(symbol):
        return None, "NOT_FOUND"
    master = _security_master_isins_for_symbol(symbol)
    equity_list = _equity_list_isins_for_symbol(symbol)
    if len(master) > 1 or len(equity_list) > 1 or (master and equity_list and master != equity_list):
        return None, "AMBIGUOUS"
    if master:
        return next(iter(master)), SYMBOL_SECURITY_MASTER
    if equity_list:
        return next(iter(equity_list)), SYMBOL_EQUITY_L
    return None, "NOT_FOUND"


def symbol_q(symbol, field="symbol"):
    """Exact symbol match; with the flag on, also the stored Yahoo form (SYMBOL.NS)."""
    bare = str(symbol or "").strip()
    query = Q(**{f"{field}__iexact": bare})
    if bare and nse_identity_matching_enabled():
        query |= Q(**{f"{field}__iexact": f"{_bare(bare)}.NS"})
    return query
