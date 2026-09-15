"""
Auto-fetches sector / P-E / P-B / PEG / ROE for a stock or ETF Asset from
Yahoo Finance, so a newly transacted security gets these fields
populated automatically.

Fields already carrying a value are never overwritten - only nulls get
filled. Yahoo Finance failures or missing values are skipped without
inventing data.
"""

import logging

import yfinance as yf

from investments.models import AssetCategory

logger = logging.getLogger(__name__)

ROE_INFO_KEY = "returnOnEquity"
ROE_SCALE = 100

FIELD_FROM_INFO = {
    "sector": "sector",
    "pe_ratio": "trailingPE",
    "pb_ratio": "priceToBook",
    "peg_ratio": "pegRatio",
}


def enrich_quant_fields(asset, security):
    """
    Fetch sector/pe_ratio/pb_ratio/peg_ratio/roe from Yahoo Finance
    for one STOCK/ETF Asset and fill whichever fields are null on
    the SecurityMaster instance.
    """

    if asset.category not in (
        AssetCategory.STOCK,
        AssetCategory.ETF,
    ):
        return False

    already_complete = (
        security.sector is not None
        and security.pe_ratio is not None
        and security.pb_ratio is not None
        and security.peg_ratio is not None
        and security.roe is not None
    )

    if already_complete:
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
        if getattr(security, model_field) is not None:
            continue

        value = info.get(info_key)
        if value is None:
            continue

        setattr(security, model_field, value)
        update_fields.append(model_field)
        changed = True

    if security.roe is None:
        roe_fraction = info.get(ROE_INFO_KEY)
        if roe_fraction is not None:
            security.roe = round(roe_fraction * ROE_SCALE, 2)
            update_fields.append("roe")
            changed = True

    if not changed:
        return False

    update_fields.append("updated_at")
    security.save(update_fields=update_fields)

    logger.info(
        "[QUANT ENRICHMENT] %s (%s): filled %s from Yahoo Finance",
        asset.name,
        asset.symbol,
        ", ".join(f for f in update_fields if f != "updated_at"),
    )

    return True
