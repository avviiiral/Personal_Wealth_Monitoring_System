"""
Fetch stock/ETF valuation and classification fields from Yahoo Finance.

The refresh is intentionally non-destructive only when Yahoo does not
return a value. When Yahoo does return a value, the current value is
updated so P/E, P/B, PEG, sector and cap classification stay current.
"""

import logging

import yfinance as yf

from investments.models import AssetCategory
from config.pwms_config import get as get_pwms_config

logger = logging.getLogger(__name__)

FIELD_FROM_INFO = {
    "sector": "sector",
    "pe_ratio": "trailingPE",
    "pb_ratio": "priceToBook",
    "peg_ratio": "pegRatio",
}


def _cap_type_from_market_cap(market_cap):
    """Classify Indian equities using market-cap bands in INR."""
    if market_cap is None:
        return None
    try:
        value = float(market_cap)
    except (TypeError, ValueError):
        return None

    bands = get_pwms_config("market_data", "equity_market_cap", {})
    if value >= bands.get("large_min", 1_000_000_000_000):
        return "Large Cap"
    if value >= bands.get("mid_min", 200_000_000_000):
        return "Mid Cap"
    return "Small Cap"


def enrich_quant_fields(asset, security, force_refresh=False):
    """Fetch and persist current Yahoo Finance stock/ETF metrics."""
    if asset.category not in (AssetCategory.STOCK, AssetCategory.ETF):
        return False

    if not asset.symbol:
        return False

    try:
        info = yf.Ticker(asset.symbol).info
    except Exception:
        logger.warning(
            "[QUANT ENRICHMENT] Yahoo Finance lookup failed for %s (%s)",
            asset.name,
            asset.symbol,
            exc_info=True,
        )
        return False

    if not info:
        return False

    changed = False
    update_fields = []

    for model_field, info_key in FIELD_FROM_INFO.items():
        value = info.get(info_key)
        if value is None:
            continue

        try:
            if model_field in {"pe_ratio", "pb_ratio", "peg_ratio"}:
                value = float(value)
        except (TypeError, ValueError):
            continue

        if getattr(security, model_field) != value:
            setattr(security, model_field, value)
            update_fields.append(model_field)
            changed = True

    cap_type = _cap_type_from_market_cap(info.get("marketCap"))
    if cap_type is not None and security.cap_type != cap_type:
        security.cap_type = cap_type
        update_fields.append("cap_type")
        changed = True

    if not changed:
        return False

    update_fields.append("updated_at")
    security.save(update_fields=update_fields)

    logger.info(
        "[QUANT ENRICHMENT] %s (%s): refreshed %s%s",
        asset.name,
        asset.symbol,
        ", ".join(update_fields[:-1]),
        " (forced)" if force_refresh else "",
    )
    return True
