import logging
import threading

from django.db import close_old_connections

logger = logging.getLogger(__name__)


def refresh_assets_async(asset_ids):
    """
    Fetch and rebuild market prices for a set of assets right after
    they were created or touched by a transaction import - so a
    newly added stock, mutual fund, or bond shows a live price
    immediately instead of waiting for the next scheduled refresh
    (up to 15 minutes for stocks/ETFs, once a day for AMFI mutual
    fund NAVs - see market_data.services).

    Runs on a background daemon thread, mirroring the existing
    MarketPriceScheduler / DailyRefreshScheduler pattern in
    market_data.services, so the import request's HTTP response
    never blocks on external network calls (Yahoo Finance, AMFI,
    NSE). Any failure here is logged, never raised - the import
    itself already succeeded and committed by the time this runs.
    """

    # De-duplicate while preserving order, and normalize away any
    # falsy/None entries a caller might pass.
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
    # Local imports: keeps this module import-safe in contexts that
    # don't need the market_data/portfolio apps loaded, and avoids
    # a circular import at module load time (investments,
    # market_data, and portfolio already reference each other's
    # models/services elsewhere in the app).
    from investments.models import Asset
    from investments.services.security_master import (
        SecurityMasterService,
    )
    from market_data.services.market_data_manager import (
        MarketDataManager,
    )
    from market_data.services.yahoo_quant_enrichment import (
        enrich_quant_fields,
    )

    # New thread, new DB connection - and since this thread outlives
    # the request that spawned it, make sure it isn't handed a
    # connection Django considers stale.
    close_old_connections()

    try:
        assets = Asset.objects.filter(id__in=asset_ids)

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
                    "[POST-IMPORT REFRESH] Failed for asset "
                    "%s (%s)",
                    asset.id,
                    asset.name,
                )

            try:
                security = (
                    SecurityMasterService
                    .get_for_asset(
                        owner=asset.owner,
                        asset=asset,
                    )
                )

                if security is not None:
                    enrich_quant_fields(asset, security)

            except Exception:
                logger.exception(
                    "[POST-IMPORT REFRESH] Quant enrichment "
                    "failed for asset %s (%s)",
                    asset.id,
                    asset.name,
                )

    finally:
        close_old_connections()
