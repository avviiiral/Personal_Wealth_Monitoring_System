"""Backfill individual MF portfolio-manager names from scheme documents.

The manager values are read from the SSD (Scheme Summary Document) group
published by the MF details endpoint. Both wrapped single-scheme responses and
the batch response are supported. Source failures are isolated from NAV refresh.
"""

import logging
import re
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
        if isinstance(value, dict):
            for key in (
                "name", "manager_name", "fund_manager_name", "person_name",
                "full_name", "individual_name", "manager",
            ):
                candidate = value.get(key)
                if isinstance(candidate, dict):
                    candidate = AMFIFundManagerService._manager_name(candidate)
                if isinstance(candidate, str) and candidate.strip():
                    value = candidate
                    break
            else:
                return None

        if not isinstance(value, str):
            return None

        name = " ".join(value.split()).strip()
        name = re.sub(r"\s*\(\s*since\s+[^)]*\)\s*$", "", name, flags=re.IGNORECASE)
        name = re.sub(r"\s*\[\s*since\s+[^]]*\]\s*$", "", name, flags=re.IGNORECASE)
        name = name.strip(" ;,|")
        return name if name and name.casefold() not in {"na", "n/a", "not available", "-"} else None

    @classmethod
    def _manager_names(cls, source):
        """Return individual names from an SSD object or flattened scheme record."""
        if not isinstance(source, dict):
            return []

        keys = (
            "managers", "fund_managers", "fund_manager",
            "manager_names", "ssd_fund_managers",
        )
        entries = next((source.get(key) for key in keys if source.get(key) not in (None, "", [])), None)
        if entries is None:
            entries = []

        if isinstance(entries, str):
            entries = re.split(r"[;\n|]+", entries)
        elif isinstance(entries, dict):
            if any(key in entries for key in (
                "name", "manager_name", "fund_manager_name", "person_name", "full_name"
            )):
                entries = [entries]
            else:
                entries = list(entries.values())
        elif not isinstance(entries, list):
            entries = [entries]

        names = []
        for entry in entries:
            values = re.split(r"[;\n|]+", entry) if isinstance(entry, str) else [entry]
            for value in values:
                name = cls._manager_name(value)
                if name and name not in names:
                    names.append(name)

        if not names:
            for key, value in source.items():
                normalized = str(key).casefold().replace("-", "_").replace(" ", "_")
                if "manager" not in normalized:
                    continue
                values = re.split(r"[;\n|]+", value) if isinstance(value, str) else [value]
                for item in values:
                    name = cls._manager_name(item)
                    if name and name not in names:
                        names.append(name)
        return names

    @staticmethod
    def _unwrap_record(record):
        """Unwrap single-scheme data envelopes used by details responses."""
        current = record
        for _ in range(3):
            if not isinstance(current, dict):
                return {}
            nested = current.get("data")
            if isinstance(nested, dict) and (
                "scheme_code" in nested or "ssd" in nested or "scheme_summary_document" in nested
            ):
                current = nested
                continue
            return current
        return current if isinstance(current, dict) else {}

    @classmethod
    def _response_records(cls, payload):
        if not isinstance(payload, dict):
            return []

        records = payload.get("data")
        if isinstance(records, list):
            return [cls._unwrap_record(record) for record in records if isinstance(record, dict)]
        if isinstance(records, dict):
            unwrapped = cls._unwrap_record(records)
            if unwrapped.get("scheme_code") or unwrapped.get("code"):
                return [unwrapped]
            nested = records.get("data")
            if isinstance(nested, list):
                return [cls._unwrap_record(record) for record in nested if isinstance(record, dict)]

        unwrapped = cls._unwrap_record(payload)
        return [unwrapped] if unwrapped.get("scheme_code") or unwrapped.get("code") else []

    @classmethod
    def _manager_website(cls, payload):
        if not isinstance(payload, dict):
            return None
        url_keys = (
            "manager_profile_url", "fund_manager_url", "profile_url",
            "scheme_summary_document_url", "document_url", "document_link",
            "ssd_url", "pdf_url", "source_url", "url",
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
        response_records = 0
        matched_schemes = 0
        for start in range(0, len(codes), cls.BATCH_SIZE):
            batch = codes[start:start + cls.BATCH_SIZE]
            try:
                response = requests.get(
                    cls.DETAILS_URL,
                    params={"schemes": ",".join(batch), "view": "full"},
                    headers={
                        "User-Agent": "PWMS-WatchList/1.0",
                        "Accept": "application/json",
                    },
                    timeout=cls.TIMEOUT_SECONDS,
                )
                response.raise_for_status()
                records = cls._response_records(response.json())
                response_records += len(records)

                for record in records:
                    code = str(record.get("scheme_code") or record.get("code") or "").strip()
                    if code not in product_ids_by_code:
                        continue
                    matched_schemes += 1
                    ssd = record.get("ssd") or record.get("scheme_summary_document") or {}
                    names = cls._manager_names(ssd)
                    if not names:
                        names = cls._manager_names(record)
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

        result = {
            "requested": len(codes),
            "batches": (len(codes) + cls.BATCH_SIZE - 1) // cls.BATCH_SIZE,
            "response_records": response_records,
            "matched_schemes": matched_schemes,
            "updated": len(manager_updates),
            "failed_batches": failed_batches,
        }
        logger.info("Mutual-fund manager enrichment completed: %s", result)
        return result
