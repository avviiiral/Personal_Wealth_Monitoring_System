import logging
import threading

from django.db import close_old_connections

logger = logging.getLogger(__name__)


def refresh_assets_async(asset_ids):
    """
    Fetch and rebuild market prices for a set of assets right after
    they were created or touched by a transaction import.

    Runs on a background daemon thread. Any failure here is logged,
    never raised, because the import itself has already committed.
    """
    asset_ids = list(dict.fromkeys(i for i in asset_ids if i))

    if not asset_ids:
        return

    thread = threading.Thread(
        target=_refresh_assets,
        args=(asset_ids,),
        name="post-import-price-refresh",
        daemon=True,
    )
    thread.start()


def _refresh_assets(asset_ids):
    from investments.models import Asset
    from investments.services.security_master import SecurityMasterService
    from market_data.services.market_data_manager import MarketDataManager
    from market_data.services.yahoo_quant_enrichment import enrich_quant_fields

    close_old_connections()

    try:
        assets = Asset.objects.filter(id__in=asset_ids).select_related(
            "owner",
            "family_group",
        )

        for asset in assets:
            try:
                result = MarketDataManager.fetch_and_rebuild(asset)
                logger.info(
                    "[POST-IMPORT REFRESH] %s: %s",
                    asset.name,
                    result,
                )
            except Exception:
                logger.exception(
                    "[POST-IMPORT REFRESH] Failed for asset %s (%s)",
                    asset.id,
                    asset.name,
                )

            try:
                security = SecurityMasterService.get_for_asset(
                    owner=asset.owner,
                    asset=asset,
                    family_group_id=asset.family_group_id,
                )

                if security is not None:
                    enrich_quant_fields(asset, security)

            except Exception:
                logger.exception(
                    "[POST-IMPORT REFRESH] Quant enrichment failed for asset %s (%s)",
                    asset.id,
                    asset.name,
                )

    finally:
        close_old_connections()
