"""Backfill individual MF portfolio-manager names from scheme documents.

The upstream endpoint used here is a third-party aggregator of AMFI Scheme
Summary Documents. It is deliberately isolated from the NAV import: a source
failure must never prevent mutual-fund discovery or performance refresh.
"""

import logging
from urllib.parse import urlparse

import requests

from config.pwms_config import get as get_pwms_config
from watchlist.models import MutualFundProduct

logger = logging.getLogger(__name__)


class AMFIFundManagerService:
    CONFIG = get_pwms_config("providers", "amfi_fund_managers", {})
    DETAILS_URL = CONFIG.get("details_url", "https://api.tigzig.com/mf/v1/details")
    BATCH_SIZE = min(max(int(CONFIG.get("batch_size", 50)), 1), 50)
    TIMEOUT_SECONDS = int(CONFIG.get("timeout_seconds", 20))

    @staticmethod
    def _valid_http_url(value):
        if not isinstance(value, str) or not value.strip():
            return None
        value = value.strip()
        try:
            parsed = urlparse(value)
        except ValueError:
            return None
        return value if parsed.scheme in {"http", "https"} and parsed.netloc else None

    @staticmethod
    def _manager_name(value):
        if isinstance(value, str):
            name = " ".join(value.split()).strip()
            return name or None
        if not isinstance(value, dict):
            return None
        for key in ("name", "manager_name", "fund_manager_name", "person_name", "manager"):
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate.strip():
                return " ".join(candidate.split()).strip()
        return None

    @classmethod
    def _manager_names(cls, ssd):
        if not isinstance(ssd, dict):
            return []

        entries = (
            ssd.get("managers")
            or ssd.get("fund_managers")
            or ssd.get("fund_manager")
            or []
        )
        if isinstance(entries, dict):
            entries = list(entries.values())
        elif not isinstance(entries, list):
            entries = [entries]

        names = []
        for entry in entries:
            name = cls._manager_name(entry)
            if name and name not in names:
                names.append(name)

        # Some source documents flatten manager fields rather than exposing
        # the list under a single "managers" property.
        if not names:
            for key, value in ssd.items():
                normalized = str(key).casefold().replace("-", "_").replace(" ", "_")
                if "manager" not in normalized or not normalized.endswith("_name"):
                    continue
                name = cls._manager_name(value)
                if name and name not in names:
                    names.append(name)
        return names

    @classmethod
    def _manager_website(cls, payload):
        if not isinstance(payload, dict):
            return None
        url_keys = (
            "manager_profile_url",
            "fund_manager_url",
            "profile_url",
            "scheme_summary_document_url",
            "document_url",
            "document_link",
            "ssd_url",
            "pdf_url",
            "source_url",
            "url",
        )
        for key in url_keys:
            candidate = cls._valid_http_url(payload.get(key))
            if candidate:
                return candidate
        return None

    @classmethod
    def refresh_missing(cls):
        """Fetch managers for missing scheme codes in batches of at most 50."""
        if not cls.DETAILS_URL:
            return {"requested": 0, "updated": 0, "failed_batches": 0}

        rows = list(
            MutualFundProduct.objects.filter(fund_manager__isnull=True)
            .exclude(scheme_code__isnull=True)
            .exclude(scheme_code="")
            .values("id", "scheme_code")
        )
        empty_rows = MutualFundProduct.objects.filter(fund_manager="").exclude(
            scheme_code__isnull=True
        ).exclude(scheme_code="").values("id", "scheme_code")
        known_ids = {row["id"] for row in rows}
        rows.extend(row for row in empty_rows if row["id"] not in known_ids)

        product_ids_by_code = {}
        for row in rows:
            code = str(row["scheme_code"]).strip()
            if code:
                product_ids_by_code.setdefault(code, []).append(row["id"])

        codes = list(product_ids_by_code)
        if not codes:
            return {"requested": 0, "updated": 0, "failed_batches": 0}

        updates_by_id = {}
        failed_batches = 0
        for start in range(0, len(codes), cls.BATCH_SIZE):
            batch = codes[start:start + cls.BATCH_SIZE]
            try:
                response = requests.get(
                    cls.DETAILS_URL,
                    params={"schemes": ",".join(batch), "view": "compact"},
                    headers={"User-Agent": "PWMS-WatchList/1.0"},
                    timeout=cls.TIMEOUT_SECONDS,
                )
                response.raise_for_status()
                payload = response.json()
                records = payload.get("data", []) if isinstance(payload, dict) else []
                if isinstance(records, dict):
                    records = [records]
                if not isinstance(records, list):
                    continue

                for record in records:
                    if not isinstance(record, dict):
                        continue
                    code = str(record.get("scheme_code") or record.get("code") or "").strip()
                    if code not in product_ids_by_code:
                        continue
                    ssd = record.get("ssd") or record.get("scheme_summary_document") or {}
                    names = cls._manager_names(ssd)
                    if not names:
                        continue
                    manager_website = cls._manager_website(ssd) or cls._manager_website(record)
                    for product_id in product_ids_by_code[code]:
                        updates_by_id[product_id] = {
                            "fund_manager": "; ".join(names),
                            "fund_manager_website": manager_website,
                        }
            except (requests.RequestException, ValueError, TypeError) as exc:
                failed_batches += 1
                logger.warning(
                    "Unable to retrieve mutual-fund managers for batch %s: %s",
                    start // cls.BATCH_SIZE + 1,
                    exc,
                )

        manager_updates = [
            MutualFundProduct(
                id=product_id,
                fund_manager=value["fund_manager"],
                fund_manager_website=value["fund_manager_website"],
            )
            for product_id, value in updates_by_id.items()
        ]
        if manager_updates:
            MutualFundProduct.objects.bulk_update(
                manager_updates,
                ["fund_manager", "fund_manager_website"],
                batch_size=500,
            )

        return {
            "requested": len(codes),
            "updated": len(manager_updates),
            "failed_batches": failed_batches,
        }
