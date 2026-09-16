import csv
import io
import os
import re
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation

import requests
from django.db import transaction
from django.utils import timezone

from watchlist.models import DiscoveryRun, InvestmentProduct, MutualFundProduct, PerformanceSnapshot, PMSProduct, ProductType
from watchlist.services.performance import AMFIPerformanceService


class AMFIUniverseService:
    NAV_URL = "https://www.amfiindia.com/spages/NAVAll.txt"
    SOURCE = "AMFI"

    @staticmethod
    def _headers():
        return {"User-Agent": "PWMS-WatchList/1.0"}

    @staticmethod
    def _decimal(value):
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
        """Parse both AMFI NAVAll formats currently encountered in the wild.

        AMFI historically published six data columns:
        scheme code, two ISIN fields, scheme name, NAV, date.
        It also introduced an eight-column variant with Plan and Option
        inserted before NAV. The parser must accept both so a feed-format
        transition cannot silently produce an empty universe.
        """
        provider = None
        records = []
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            parts = [cls._normalize(part) for part in line.split(";")]
            if not parts[0].isdigit():
                if parts[0].lower() != "scheme code":
                    provider = parts[0]
                continue
            if len(parts) < 6:
                continue

            # Eight-column format:
            # code;isin1;isin2;name;plan;option;nav;date
            if len(parts) >= 8:
                nav = cls._decimal(parts[6])
                try:
                    nav_date = datetime.strptime(parts[7], "%d-%b-%Y").date()
                except (ValueError, TypeError):
                    nav = None
                if nav is not None and nav_date is not None:
                    name = parts[3]
                    isin1 = parts[1] if parts[1] not in {"-", ""} else None
                    isin2 = parts[2] if parts[2] not in {"-", ""} else None
                    growth_isin = isin1 if "growth" in name.lower() and "idcw" not in name.lower() else None
                    selected_isin = growth_isin or isin1 or isin2
                    records.append({
                        "scheme_code": parts[0], "name": name, "provider": provider,
                        "plan": parts[4] or None, "option": parts[5] or None,
                        "isin": selected_isin, "nav": nav, "date": nav_date,
                    })
                    continue

            # Six-column format:
            # code;isin1;isin2;name;nav;date
            nav = cls._decimal(parts[4])
            if nav is None:
                continue
            try:
                nav_date = datetime.strptime(parts[5], "%d-%b-%Y").date()
            except (ValueError, TypeError):
                continue
            name = parts[3]
            isin1 = parts[1] if parts[1] not in {"-", ""} else None
            isin2 = parts[2] if parts[2] not in {"-", ""} else None
            growth_isin = isin1 if "growth" in name.lower() and "idcw" not in name.lower() else None
            selected_isin = growth_isin or isin1 or isin2
            records.append({
                "scheme_code": parts[0], "name": name, "provider": provider,
                "plan": None, "option": None,
                "isin": selected_isin, "nav": nav, "date": nav_date,
            })
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
                    product, created = InvestmentProduct.objects.update_or_create(
                        identity_key=cls.identity(record),
                        defaults={
                            "product_type": ProductType.MUTUAL_FUND,
                            "name": record["name"], "provider": record.get("provider"), "country": "India",
                            "isin": record.get("isin"), "external_identifier": record.get("scheme_code"),
                            "currency": "INR", "source": cls.SOURCE, "source_reference": cls.NAV_URL,
                            "source_date": record["date"], "is_active": True,
                        },
                    )
                    MutualFundProduct.objects.update_or_create(
                        product=product,
                        defaults={"scheme_code": record["scheme_code"], "plan": record.get("plan"), "option": record.get("option"), "latest_nav": record["nav"], "latest_nav_date": record["date"]},
                    )
                    PerformanceSnapshot.objects.update_or_create(
                        product=product, date=record["date"], source=cls.SOURCE,
                        defaults={"nav_or_value": record["nav"], "source_reference": cls.NAV_URL},
                    )
                    discovered += 1
                    updated += int(not created)
                except Exception:
                    failed += 1
            performance = AMFIPerformanceService.refresh()
            run.discovered, run.updated, run.failed = discovered, updated, failed
            run.details = {"source_reference": cls.NAV_URL, "performance": performance}
            run.finished_at = timezone.now()
            run.save(update_fields=["discovered", "updated", "failed", "details", "finished_at"])
            return {"discovered": discovered, "updated": updated, "failed": failed, "performance": performance}
        except Exception as exc:
            run.failed = 1
            run.details = {"error": str(exc)}
            run.finished_at = timezone.now()
            run.save(update_fields=["failed", "details", "finished_at"])
            raise


class PMSDiscoveryService:
    """Generic JSON/CSV adapter for authoritative PMS source endpoints.

    Source URLs are deployment configuration, not code-level provider mappings.
    The adapter accepts records using the conceptual PMS fields in the Watch
    List model and ignores unknown fields. Missing fields remain null.
    """

    SOURCE = "PMS_CONFIGURED_SOURCE"

    @classmethod
    def configured_sources(cls):
        return [u.strip() for u in os.getenv("WATCHLIST_PMS_SOURCE_URLS", "").split(",") if u.strip()]

    @staticmethod
    def _records(response):
        content_type = response.headers.get("Content-Type", "").lower()
        if "json" in content_type or response.text.lstrip().startswith(("[", "{")):
            payload = response.json()
            if isinstance(payload, dict):
                payload = payload.get("results") or payload.get("data") or []
            return payload if isinstance(payload, list) else []
        return list(csv.DictReader(io.StringIO(response.text)))

    @classmethod
    def refresh(cls):
        sources = cls.configured_sources()
        if not sources:
            return {"discovered": 0, "updated": 0, "failed": 0, "configured_sources": [], "message": "No authoritative PMS source configured; no PMS values were fabricated."}
        discovered = updated = failed = 0
        for source_url in sources:
            run = DiscoveryRun.objects.create(source=cls.SOURCE)
            try:
                response = requests.get(source_url, headers={"User-Agent": "PWMS-WatchList/1.0"}, timeout=60)
                response.raise_for_status()
                for row in cls._records(response):
                    try:
                        name = str(row.get("pms_name") or row.get("name") or row.get("strategy_name") or "").strip()
                        if not name:
                            continue
                        identifier = str(row.get("external_identifier") or row.get("strategy_id") or name).strip()
                        product, created = InvestmentProduct.objects.update_or_create(
                            identity_key=f"PMS:{identifier.upper()}",
                            defaults={
                                "product_type": ProductType.PMS, "name": name,
                                "provider": row.get("provider") or row.get("pms_provider"),
                                "country": row.get("country"), "category": row.get("category"),
                                "sub_category": row.get("sub_category"), "external_identifier": identifier,
                                "currency": row.get("currency") or "INR", "source": cls.SOURCE,
                                "source_reference": source_url, "official_website": row.get("official_website"),
                            },
                        )
                        PMSProduct.objects.update_or_create(
                            product=product,
                            defaults={
                                "strategy_name": row.get("strategy_name") or name, "strategy_type": row.get("strategy_type"),
                                "asset_class": row.get("asset_class"), "benchmark": row.get("benchmark"),
                                "aum": cls._decimal(row.get("aum")), "minimum_investment": cls._decimal(row.get("minimum_investment")),
                                "latest_value": cls._decimal(row.get("latest_value")), "risk_information": row.get("risk_information"),
                            },
                        )
                        source_date = None
                        raw_date = row.get("source_date") or row.get("date")
                        if raw_date:
                            try:
                                source_date = datetime.fromisoformat(str(raw_date)[:10]).date()
                            except ValueError:
                                source_date = None
                        if source_date:
                            product.source_date = source_date
                            product.save(update_fields=["source_date", "updated_at"])
                        discovered += 1
                        updated += int(not created)
                    except Exception:
                        failed += 1
                run.details = {"source_reference": source_url}
            except Exception as exc:
                failed += 1
                run.details = {"source_reference": source_url, "error": str(exc)}
            finally:
                run.discovered, run.updated, run.failed = discovered, updated, failed
                run.finished_at = timezone.now()
                run.save(update_fields=["discovered", "updated", "failed", "details", "finished_at"])
        return {"discovered": discovered, "updated": updated, "failed": failed, "configured_sources": sources}
