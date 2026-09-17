"""Background Watch List refresh with network work outside DB transactions.

The normal Watch List services use atomic refreshes for manual/management-command
use. The long-running background worker deliberately avoids those wrappers so
SQLite is only held for individual short ORM writes; external AMFI/APMI requests
never hold a database transaction.
"""

import logging
from datetime import date
from decimal import Decimal

from django.db import close_old_connections
from django.utils import timezone

from config.database_scheduler_lock import DATABASE_SCHEDULER_LOCK
from watchlist.models import (
    DiscoveryRun,
    InvestmentProduct,
    MutualFundProduct,
    PerformanceSnapshot,
    PMSProduct,
    ProductType,
)
from watchlist.services.performance import AMFIPerformanceService
from watchlist.services.pms import APMIPMSDiscoveryService
from watchlist.services.universe import AMFIUniverseService

logger = logging.getLogger(__name__)


class BackgroundWatchListRefreshService:
    """Fetch first, then persist with short autocommit DB operations.

    SQLite has a single writer. The worker remains a dedicated thread, but all
    background database writers share DATABASE_SCHEDULER_LOCK so AMFI/APMI,
    market prices, daily refreshes, and portfolio-news persistence never try to
    write SQLite concurrently. The lock is intentionally held only by this
    background worker while it is persisting; network calls are completed before
    each refresh phase starts.
    """

    @classmethod
    def refresh_mutual_funds(cls):
        run = DiscoveryRun.objects.create(source="AMFI")
        discovered = updated = failed = 0
        try:
            # Network + parsing happen before the first product write.
            records = AMFIUniverseService.parse_latest_feed(
                AMFIUniverseService.download_latest()
            )
            for record in records:
                try:
                    product, created = InvestmentProduct.objects.update_or_create(
                        identity_key=AMFIUniverseService.identity(record),
                        defaults={
                            "product_type": ProductType.MUTUAL_FUND,
                            "name": record["name"],
                            "provider": record.get("provider"),
                            "country": "India",
                            "category": record.get("category"),
                            "isin": record.get("isin"),
                            "external_identifier": record.get("scheme_code"),
                            "currency": "INR",
                            "source": "AMFI",
                            "source_reference": AMFIUniverseService.NAV_URL,
                            "source_date": record["date"],
                            "is_active": True,
                        },
                    )
                    MutualFundProduct.objects.update_or_create(
                        product=product,
                        defaults={
                            "scheme_code": record["scheme_code"],
                            "plan": record.get("plan"),
                            "option": record.get("option"),
                            "fund_type": record.get("category"),
                            "latest_nav": record["nav"],
                            "latest_nav_date": record["date"],
                        },
                    )
                    PerformanceSnapshot.objects.update_or_create(
                        product=product,
                        date=record["date"],
                        source="AMFI",
                        defaults={
                            "nav_or_value": record["nav"],
                            "source_reference": AMFIUniverseService.NAV_URL,
                        },
                    )
                    discovered += 1
                    updated += int(not created)
                except Exception:
                    failed += 1
                    logger.exception("Background AMFI write failed for %s", record.get("name"))

            performance = cls.refresh_performance()
            run.discovered = discovered
            run.updated = updated
            run.failed = failed
            run.details = {
                "source_reference": AMFIUniverseService.NAV_URL,
                "background_mode": True,
                "performance": performance,
            }
            run.finished_at = timezone.now()
            run.save(update_fields=["discovered", "updated", "failed", "details", "finished_at"])
            return {
                "discovered": discovered,
                "updated": updated,
                "failed": failed,
                "performance": performance,
            }
        except Exception as exc:
            run.failed = max(1, failed)
            run.details = {"error": str(exc), "background_mode": True}
            run.finished_at = timezone.now()
            run.save(update_fields=["failed", "details", "finished_at"])
            raise

    @classmethod
    def refresh_performance(cls):
        """Backfill MF history without holding a transaction during HTTP calls."""
        products = list(
            InvestmentProduct.objects.filter(
                product_type=ProductType.MUTUAL_FUND,
                is_active=True,
            ).select_related("mutual_fund")
        )
        product_ids = [product.id for product in products]
        latest_snapshots = {}
        for snapshot in PerformanceSnapshot.objects.filter(
            product_id__in=product_ids,
            source="AMFI",
        ).order_by("-date"):
            latest_snapshots.setdefault(snapshot.product_id, snapshot)

        needing = [
            product for product in products
            if latest_snapshots.get(product.id)
            and any(
                getattr(latest_snapshots[product.id], field) is None
                for field, _ in AMFIPerformanceService.PERIODS
            )
        ]
        if not needing:
            return {
                "products": len(products),
                "history_requests": 0,
                "snapshots": 0,
                "metrics_updated": 0,
                "failed": 0,
            }

        latest_date = max(snapshot.date for snapshot in latest_snapshots.values())
        # All six HTTP requests happen before the corresponding DB writes.
        history, failed = AMFIPerformanceService._load_required_history(
            needing, latest_date
        )
        snapshots_written = metrics_updated = 0

        for product in needing:
            latest = latest_snapshots.get(product.id)
            if latest is None:
                continue
            periods = history.get(str(product.external_identifier or ""), {})
            values = {}
            for field, _ in AMFIPerformanceService.PERIODS:
                record = periods.get(field)
                if not record:
                    continue
                PerformanceSnapshot.objects.update_or_create(
                    product=product,
                    date=record["date"],
                    source="AMFI",
                    defaults={
                        "nav_or_value": record["nav"],
                        "source_reference": AMFIPerformanceService.HISTORY_URL,
                    },
                )
                snapshots_written += 1
                values[field] = AMFIPerformanceService._return_percent(
                    latest.nav_or_value, record["nav"]
                )

            if values:
                update_fields = []
                for field, value in values.items():
                    setattr(latest, field, value)
                    update_fields.append(field)

                for field, _months in reversed(AMFIPerformanceService.PERIODS):
                    record = periods.get(field)
                    if not record or record["nav"] in (None, Decimal("0")) or latest.nav_or_value in (None, Decimal("0")):
                        continue
                    years = Decimal(str((latest.date - record["date"]).days)) / Decimal("365.2425")
                    if years > 0:
                        latest.cagr = (
                            (latest.nav_or_value / record["nav"]) ** (Decimal("1") / years)
                            - Decimal("1")
                        ) * Decimal("100")
                        update_fields.append("cagr")
                        break

                latest.save(update_fields=list(dict.fromkeys(update_fields)))
                metrics_updated += 1

        return {
            "products": len(products),
            "history_requests": len(AMFIPerformanceService.PERIODS),
            "snapshots": snapshots_written,
            "metrics_updated": metrics_updated,
            "failed": failed,
        }

    @classmethod
    def refresh_pms(cls):
        run = DiscoveryRun.objects.create(source="APMI")
        discovered = updated = failed = 0
        try:
            # APMI HTTP fetch and HTML parsing happen before any DB writes.
            response = __import__("requests").get(
                APMIPMSDiscoveryService.REPORT_URL,
                headers=APMIPMSDiscoveryService._headers(),
                timeout=60,
            )
            response.raise_for_status()
            report_date = APMIPMSDiscoveryService._report_date(response.text)
            records = APMIPMSDiscoveryService._records(response.text)

            for record in records:
                try:
                    identity_suffix = record["iaid"] or record["name"].upper()
                    identity = f"PMS:APMI:{record['provider'].upper()}:{identity_suffix}"
                    product, created = InvestmentProduct.objects.update_or_create(
                        identity_key=identity,
                        defaults={
                            "product_type": ProductType.PMS,
                            "name": record["name"],
                            "provider": record["provider"],
                            "country": "India",
                            "category": "PMS",
                            "sub_category": "Investment Approach",
                            "external_identifier": record["iaid"],
                            "currency": "INR",
                            "source": "APMI",
                            "source_reference": APMIPMSDiscoveryService.REPORT_URL,
                            "source_date": report_date,
                            "is_active": True,
                        },
                    )
                    PMSProduct.objects.update_or_create(
                        product=product,
                        defaults={
                            "strategy_name": record["name"],
                            "strategy_type": "Investment Approach",
                            "asset_class": "PMS",
                            "aum": record["aum"],
                            "latest_value": record["aum"],
                        },
                    )
                    PerformanceSnapshot.objects.update_or_create(
                        product=product,
                        date=report_date,
                        source="APMI",
                        defaults={
                            "nav_or_value": record["aum"],
                            "aum": record["aum"],
                            "return_1m": record["performance"].get("1m"),
                            "return_3m": record["performance"].get("3m"),
                            "return_6m": record["performance"].get("6m"),
                            "return_1y": record["performance"].get("1y"),
                            "return_3y": record["performance"].get("3y"),
                            "return_5y": record["performance"].get("5y"),
                            "return_since_inception": record["performance"].get("si"),
                            "source_reference": APMIPMSDiscoveryService.REPORT_URL,
                        },
                    )
                    discovered += 1
                    updated += int(not created)
                except Exception:
                    failed += 1
                    logger.exception("Background APMI write failed for %s", record.get("name"))

            run.discovered = discovered
            run.updated = updated
            run.failed = failed
            run.details = {
                "source_reference": APMIPMSDiscoveryService.REPORT_URL,
                "report_date": report_date.isoformat(),
                "records_seen": len(records),
                "background_mode": True,
            }
            run.finished_at = timezone.now()
            run.save(update_fields=["discovered", "updated", "failed", "details", "finished_at"])
            return {
                "discovered": discovered,
                "updated": updated,
                "failed": failed,
                "report_date": report_date.isoformat(),
                "source": "APMI",
            }
        except Exception as exc:
            run.failed = max(1, failed)
            run.details = {"source_reference": APMIPMSDiscoveryService.REPORT_URL, "error": str(exc), "background_mode": True}
            run.finished_at = timezone.now()
            run.save(update_fields=["failed", "details", "finished_at"])
            raise

    @classmethod
    def refresh(cls):
        close_old_connections()
        try:
            logger.info("Background Watch List refresh started (network-first mode).")

            # SQLite supports concurrent readers but serializes writers. This
            # worker is one of several background writers, so serialize the
            # background write phases in-process. This does NOT block API code;
            # it only prevents background schedulers from fighting each other.
            with DATABASE_SCHEDULER_LOCK:
                mf_result = cls.refresh_mutual_funds()
                close_old_connections()
                pms_result = cls.refresh_pms()

            logger.info("Background Watch List refresh completed successfully.")
            return {"mutual_funds": mf_result, "pms": pms_result}
        finally:
            close_old_connections()
