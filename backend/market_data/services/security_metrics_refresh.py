import logging

from django.db import close_old_connections

from investments.models import Asset, AssetCategory
from investments.services.security_master import SecurityMasterService
from market_data.services.security_resolver import SecurityResolver
from market_data.services.yahoo_quant_enrichment import enrich_quant_fields

logger = logging.getLogger(__name__)


def refresh_security_metrics():
    """Refresh stock/ETF valuation and classification fields from Yahoo Finance."""
    close_old_connections()
    try:
        assets = (
            Asset.objects
            .filter(
                is_active=True,
                category__in=(AssetCategory.STOCK, AssetCategory.ETF),
            )
            .select_related("security_master")
            .order_by("id")
        )

        refreshed = 0
        failed = 0
        skipped = 0

        for asset in assets.iterator():
            try:
                resolved_symbol = SecurityResolver.resolve_yahoo_symbol(
                    symbol=asset.symbol,
                    isin=asset.isin,
                    name=asset.name,
                )
                if not asset.symbol:
                    asset.symbol = resolved_symbol

                security = asset.security_master or SecurityMasterService.get_or_create(
                    owner=asset.owner,
                    asset=asset,
                )

                asset_updates = []
                if resolved_symbol and asset.symbol != resolved_symbol:
                    asset.symbol = resolved_symbol
                    asset_updates.append("symbol")
                if asset.security_master_id != security.id:
                    asset.security_master = security
                    asset_updates.append("security_master")
                if asset_updates:
                    asset_updates.append("updated_at")
                    asset.save(update_fields=asset_updates)

                if enrich_quant_fields(asset, security, force_refresh=True):
                    refreshed += 1
                else:
                    skipped += 1
            except Exception:
                failed += 1
                logger.exception(
                    "[SECURITY METRICS REFRESH] Failed for asset %s (%s)",
                    asset.id,
                    asset.name,
                )

        logger.info(
            "[SECURITY METRICS REFRESH] completed: refreshed=%s skipped=%s failed=%s",
            refreshed,
            skipped,
            failed,
        )
        return {"refreshed": refreshed, "skipped": skipped, "failed": failed}
    finally:
        close_old_connections()
