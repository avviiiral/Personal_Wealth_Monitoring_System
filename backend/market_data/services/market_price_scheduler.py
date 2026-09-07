import logging
import threading
import time
from datetime import datetime

from django.db import close_old_connections

from investments.models import Asset
from market_data.services.market_data_manager import (
    MarketDataManager,
)


logger = logging.getLogger(__name__)


UPDATE_INTERVAL_SECONDS = 15 * 60


class MarketPriceScheduler:

    _started = False
    _lock = threading.Lock()

    @classmethod
    def start(cls):

        with cls._lock:

            if cls._started:
                return

            cls._started = True

            thread = threading.Thread(
                target=cls._run,
                name="market-price-scheduler",
                daemon=True,
            )

            thread.start()

            logger.info(
                "Market price scheduler started. "
                "Update interval: 15 minutes."
            )

    @classmethod
    def _run(cls):

        # Give Django time to finish startup.
        time.sleep(10)

        while True:

            try:

                close_old_connections()

                cls.update_prices()

            except Exception as exc:

                logger.exception(
                    "Market price scheduler failed: %s",
                    exc,
                )

            finally:

                close_old_connections()

            time.sleep(
                UPDATE_INTERVAL_SECONDS
            )

    @classmethod
    def update_prices(cls):

        assets = (
            Asset.objects
            .filter(
                category__in=[
                    "STOCK",
                    "ETF",
                    "MUTUAL_FUND",
                    "BOND",
                ]
            )
            .select_related(
                "owner"
            )
        )

        total_assets = assets.count()

        logger.info(
            "Found %s STOCK/ETF/MUTUAL_FUND/BOND assets.",
            total_assets,
        )

        updated = 0
        skipped = 0
        failed = 0
        total_records = 0

        for asset in assets:

            try:

                result = (
                    MarketDataManager
                    .fetch_and_rebuild(
                        asset=asset
                    )
                )

                if result.get("success"):

                    if result.get("skipped"):

                        skipped += 1

                        logger.info(
                            "Market price skipped for %s: %s",
                            asset.name,
                            result.get("reason"),
                        )

                    else:

                        updated += 1

                        records = result.get(
                            "records",
                            0,
                        )

                        total_records += records

                        current_price = result.get(
                            "current_price"
                        )

                        source = result.get(
                            "source",
                            "MARKET_DATA",
                        )

                        logger.info(
                            "Market price updated for %s: "
                            "source=%s, records=%s, price=%s",
                            asset.name,
                            source,
                            records,
                            current_price,
                        )

                else:

                    failed += 1

                    logger.warning(
                        "Market price update failed for %s: %s",
                        asset.name,
                        result.get("error")
                        or result.get("reason"),
                    )

            except Exception as exc:

                failed += 1

                logger.exception(
                    "Unable to update market price for %s: %s",
                    asset.name,
                    exc,
                )

        logger.info(
            "Market update completed - updated=%s, skipped=%s, "
            "failed=%s, records=%s",
            updated,
            skipped,
            failed,
            total_records,
        )