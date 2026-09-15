import re
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

import requests
from django.db import transaction
from django.utils import timezone

from watchlist.models import (
    DiscoveryRun,
    InvestmentProduct,
    MutualFundProduct,
    PerformanceSnapshot,
    ProductType,
)


class AMFIUniverseService:
    """Discover Indian mutual-fund products from AMFI's authoritative NAV feed.

    AMFI's feed is intentionally used as the discovery boundary: it gives the
    application a complete, data-driven Indian MF universe without maintaining
    AMC/fund mappings in source code. Metadata not present in the feed remains
    null rather than being guessed.
    """

    NAV_URL = "https://www.amfiindia.com/spages/NAVAll.txt"
    SOURCE = "AMFI"

    @staticmethod
    def _headers():
        return {"User-Agent": "PWMS/WatchList (+https://github.com/avviiiral/Personal_Wealth_Monitoring_System)"}

    @classmethod
    def _decimal(cls, value):
        if value in (None, "", "-"):
            return None
        try:
            return Decimal(str(value).replace(",", "").strip())
        except (InvalidOperation, ValueError, TypeError):
            return None

    @staticmethod
    def _normalize(value):
        return re.sub(r"\s+", " ", str(value or "").replace("\ufeff", "")).strip()

    @classmethod
    def parse_latest_feed(cls, text):
        """Parse the semicolon-delimited AMFI feed and retain generic AMC groups."""
        provider = None
        records = []
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            parts = [cls._normalize(part) for part in line.split(";")]
            if len(parts) < 8:
                if line and not line.startswith("Scheme Code"):
                    provider = cls._normalize(line)
                continue
            if not parts[0].isdigit():
                if parts[0].lower() != "scheme code":
                    provider = parts[0]
                continue
            nav = cls._decimal(parts[6])
            if nav is None:
                continue
            try:
                nav_date = date.fromisoformat(
                    timezone.datetime.strptime(parts[7], "%d-%b-%Y").date().isoformat()
                )
            except (ValueError, TypeError):
                continue
            name = parts[3]
            plan = parts[4] or None
            option = parts[5] or None
            isin1 = parts[1] if parts[1] not in {"-", ""} else None
            isin2 = parts[2] if parts[2] not in {"-", ""} else None
            growth_isin = isin1 if "growth" in name.lower() and "idcw" not in name.lower() else None
            dividend_isin = isin1 if growth_isin is None else None
            if dividend_isin is None and isin2:
                dividend_isin = isin2
            records.append(
                {
                    "scheme_code": parts[0],
                    "name": name,
                    "provider": provider,
                    "plan": plan,
                    "option": option,
                    "isin": growth_isin or dividend_isin,
                    "isin_growth": growth_isin,
                    "isin_dividend": dividend_isin,
                    "nav": nav,
                    "date": nav_date,
                }
            )
        return records

    @classmethod
    def download_latest(cls):
        response = requests.get(cls.NAV_URL, headers=cls._headers(), timeout=60)
        response.raise_for_status()
        return response.text

    @staticmethod
    def identity(record):
        if record.get("isin"):
            return f"MUTUAL_FUND:ISIN:{record['isin'].upper()}"
        return f"MUTUAL_FUND:SCHEME:{record['scheme_code']}"

    @classmethod
    @transaction.atomic
    def refresh(cls):
        run = DiscoveryRun.objects.create(source=cls.SOURCE)
        discovered = updated = failed = 0
        try:
            records = cls.parse_latest_feed(cls.download_latest())
            for record in records:
                try:
                    identity = cls.identity(record)
                    product, created = InvestmentProduct.objects.update_or_create(
                        identity_key=identity,
                        defaults={
                            "product_type": ProductType.MUTUAL_FUND,
                            "name": record["name"],
                            "provider": record.get("provider"),
                            "country": "India",
                            "isin": record.get("isin"),
                            "external_identifier": record.get("scheme_code"),
                            "currency": "INR",
                            "source": cls.SOURCE,
                            "source_reference": cls.NAV_URL,
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
                            "latest_nav": record["nav"],
                            "latest_nav_date": record["date"],
                        },
                    )
                    PerformanceSnapshot.objects.update_or_create(
                        product=product,
                        date=record["date"],
                        source=cls.SOURCE,
                        defaults={
                            "nav_or_value": record["nav"],
                            "source_reference": cls.NAV_URL,
                        },
                    )
                    discovered += 1
                    updated += int(not created)
                except Exception:
                    failed += 1
            run.discovered = discovered
            run.updated = updated
            run.failed = failed
            run.details = {"source_reference": cls.NAV_URL}
            run.finished_at = timezone.now()
            run.save(update_fields=["discovered", "updated", "failed", "details", "finished_at"])
            return {"discovered": discovered, "updated": updated, "failed": failed}
        except Exception as exc:
            run.failed = 1
            run.details = {"error": str(exc)}
            run.finished_at = timezone.now()
            run.save(update_fields=["failed", "details", "finished_at"])
            raise

    @classmethod
    def calculate_returns(cls, product):
        """Calculate product returns only from stored NAV observations."""
        latest = product.performance_snapshots.order_by("-date").first()
        if not latest or latest.nav_or_value is None:
            return {}
        target_days = {"return_1d": 1, "return_1w": 7, "return_1m": 30, "return_3m": 90, "return_6m": 180, "return_1y": 365, "return_3y": 1095, "return_5y": 1825}
        snapshots = list(product.performance_snapshots.order_by("date"))
        values = {item.date: item.nav_or_value for item in snapshots if item.nav_or_value is not None}
        result = {}
        for field, days in target_days.items():
            target = latest.date - timedelta(days=days)
            candidates = [d for d in values if d <= target]
            if not candidates:
                continue
            base_date = max(candidates)
            base = values[base_date]
            if base in (None, 0):
                continue
            result[field] = ((latest.nav_or_value / base) - 1) * Decimal("100")
        if "return_1y" in result:
            result["cagr"] = result["return_1y"]
        PerformanceSnapshot.objects.filter(pk=latest.pk).update(**result)
        return result


class PMSDiscoveryService:
    """Provider-neutral PMS discovery hook.

    PMS data is not standardized like AMFI's MF NAV feed. PWMS therefore does
    not invent a provider list. Deployments can supply authoritative source
    endpoints through WATCHLIST_PMS_SOURCE_URLS and implement a parser for the
    returned schema without changing the product model or ownership logic.
    """

    SOURCE = "PMS_CONFIGURED_SOURCE"

    @classmethod
    def configured_sources(cls):
        import os
        return [u.strip() for u in os.getenv("WATCHLIST_PMS_SOURCE_URLS", "").split(",") if u.strip()]

    @classmethod
    def refresh(cls):
        return {
            "discovered": 0,
            "updated": 0,
            "failed": 0,
            "configured_sources": cls.configured_sources(),
            "message": "No PMS provider was imported because no authoritative source adapter is configured.",
        }
