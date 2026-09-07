"""
Auto-fetches sector / P-E / P-B / ROE for a stock or ETF Asset from
Yahoo Finance, so a newly transacted security gets these fields
populated automatically instead of needing manual per-security
research (see investments/data/security_master_lookups.json, which
this supersedes for these four fields going forward - it can still
be used as a manual override/correction).

Deliberately does NOT cover:
    - cap_type: Yahoo's raw market-cap figure isn't the same thing
      as India's official AMFI Large/Mid/Small classification (a
      rank within the market, not a rupee threshold) - see
      investments/management/commands/import_amfi_cap_classification.py.
    - amc_name: not applicable to stocks; for mutual funds this is
      derived structurally from the scheme name instead - see
      investments/services/amc_name_resolver.py.
    - credit_rating / ytm / modified_duration / average_maturity:
      bond-specific figures Yahoo's free equity API doesn't carry.
      No free automated source is wired up for these; they remain
      a manual gap.

Like every other write in this file's spirit (see
SecurityMasterService), a field already carrying a value is never
overwritten - only nulls get filled in. A Yahoo Finance failure
(network error, ticker not covered, missing field) is logged and
skipped; it never raises into the caller, and never invents a
value PWMS didn't actually receive from Yahoo.
"""

import logging

import yfinance as yf

from investments.models import AssetCategory

logger = logging.getLogger(__name__)

# yfinance's Ticker.info returns returnOnEquity as a fraction
# (0.235), but SecurityMaster.roe (and every other stored ratio in
# this project) is a percentage number (23.5) - see
# security_master_lookups.json's own entries for the convention
# this matches.
ROE_INFO_KEY = "returnOnEquity"
ROE_SCALE = 100

FIELD_FROM_INFO = {
    "sector": "sector",
    "pe_ratio": "trailingPE",
    "pb_ratio": "priceToBook",
}


def enrich_quant_fields(asset, security):
    """
    Fetch sector/pe_ratio/pb_ratio/roe from Yahoo Finance for one
    STOCK/ETF Asset and fill in whichever of those fields are
    currently null on `security` (a SecurityMaster instance).
    Saves only if something actually changed. Returns True if a
    save happened, False otherwise (nothing to fill, or the fetch
    failed/returned nothing usable).
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
            "[QUANT ENRICHMENT] Yahoo Finance lookup failed for "
            "%s (%s)",
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
