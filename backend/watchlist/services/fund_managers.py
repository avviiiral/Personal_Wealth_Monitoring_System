"""Backfill individual MF portfolio-manager names from scheme documents.

The manager values are read from the SSD (Scheme Summary Document) group
published by the MF details endpoint. Both wrapped single-scheme responses and
the batch response are supported. Source failures are isolated from NAV refresh.
"""

import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import timedelta
from urllib.parse import urlparse

import requests
from django.db.models import Q
from django.utils import timezone

from config.pwms_config import get as get_pwms_config
from watchlist.models import MutualFundProduct

logger = logging.getLogger(__name__)


class AMFIFundManagerService:
    CONFIG = get_pwms_config("providers", "amfi_fund_managers", {})
    DETAILS_URL = CONFIG.get("details_url", "https://api.tigzig.com/mf/v1/details")
    BATCH_SIZE = min(max(int(CONFIG.get("batch_size", 50)), 1), 50)
    TIMEOUT_SECONDS = int(CONFIG.get("timeout_seconds", 20))
    MAX_WORKERS = 4
    RETRY_AFTER = timedelta(days=7)

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
                "full_name", "individual_name", "manager", "fund_manager", "person",
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
            "managers", "fund_managers", "fund_manager", "manager",
            "portfolio_managers", "manager_names", "ssd_fund_managers",
        )
        entries = next((source.get(key) for key in keys if source.get(key) not in (None, "", [])), None)
        if entries is None:
            entries = []

        if isinstance(entries, str):
            entries = re.split(r"[;\n|]+", entries)
        elif isinstance(entries, dict):
            if any(key in entries for key in (
                "name", "manager_name", "fund_manager_name", "person_name", "full_name",
                "manager", "fund_manager", "person"
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
    def _fetch_batch(cls, batch):
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
        return cls._response_records(response.json())

    @classmethod
    def refresh_missing(cls):
        """Enrich missing managers using bounded concurrency and a retry cache.

        Successfully checked schemes are not requested again for seven days,
        including schemes for which the provider has no manager details. Failed
        HTTP/API batches are left eligible for the next refresh.
        """
        if not cls.DETAILS_URL:
            return {
                "requested": 0, "checked": 0, "updated": 0,
                "failed_batches": 0,
            }

        retry_before = timezone.now() - cls.RETRY_AFTER
        rows = list(
            MutualFundProduct.objects.filter(
                Q(fund_manager__isnull=True) | Q(fund_manager="")
            ).filter(
                Q(fund_manager_checked_at__isnull=True)
                | Q(fund_manager_checked_at__lte=retry_before)
            ).exclude(
                scheme_code__isnull=True
            ).exclude(
                scheme_code=""
            ).values(
                "id", "scheme_code", "fund_manager", "fund_manager_website"
            )
        )

        products_by_code = {}
        for row in rows:
            code = str(row["scheme_code"]).strip()
            if code:
                products_by_code.setdefault(code, []).append(row)

        codes = list(products_by_code)
        if not codes:
            result = {
                "requested": 0, "checked": 0, "updated": 0,
                "failed_batches": 0,
            }
            logger.info("Mutual-fund manager enrichment skipped: %s", result)
            return result

        batches = [
            codes[start:start + cls.BATCH_SIZE]
            for start in range(0, len(codes), cls.BATCH_SIZE)
        ]
        updates_by_id = {}
        failed_batches = 0
        response_records = 0
        matched_schemes = 0
        updated_count = 0

        # Provider calls are independent. Keep concurrency bounded to avoid
        # overwhelming the upstream service while avoiding serial batch waits.
        with ThreadPoolExecutor(max_workers=min(cls.MAX_WORKERS, len(batches))) as executor:
            futures = {
                executor.submit(cls._fetch_batch, batch): batch
                for batch in batches
            }
            for future in as_completed(futures):
                batch = futures[future]
                try:
                    records = future.result()
                except (requests.RequestException, ValueError, TypeError) as exc:
                    failed_batches += 1
                    logger.warning(
                        "Unable to retrieve mutual-fund managers for batch "
                        "(first_scheme=%s, size=%s): %s",
                        batch[0] if batch else "unknown",
                        len(batch),
                        exc,
                    )
                    continue

                response_records += len(records)
                manager_data_by_code = {}
                for record in records:
                    code = str(record.get("scheme_code") or record.get("code") or "").strip()
                    if code not in products_by_code or code not in batch:
                        continue
                    matched_schemes += 1
                    ssd = record.get("ssd") or record.get("scheme_summary_document") or {}
                    names = cls._manager_names(ssd) or cls._manager_names(record)
                    if names:
                        manager_data_by_code[code] = {
                            "fund_manager": "; ".join(names),
                            "fund_manager_website": (
                                cls._manager_website(ssd)
                                or cls._manager_website(record)
                            ),
                        }

                checked_at = timezone.now()
                for code in batch:
                    manager_data = manager_data_by_code.get(code)
                    for row in products_by_code.get(code, []):
                        updates_by_id[row["id"]] = MutualFundProduct(
                            id=row["id"],
                            fund_manager=(
                                manager_data["fund_manager"]
                                if manager_data
                                else row["fund_manager"]
                            ),
                            fund_manager_website=(
                                manager_data["fund_manager_website"]
                                if manager_data
                                else row["fund_manager_website"]
                            ),
                            fund_manager_checked_at=checked_at,
                        )
                        if manager_data:
                            updated_count += 1

        checked_updates = list(updates_by_id.values())
        if checked_updates:
            MutualFundProduct.objects.bulk_update(
                checked_updates,
                [
                    "fund_manager",
                    "fund_manager_website",
                    "fund_manager_checked_at",
                ],
                batch_size=500,
            )

        result = {
            "requested": len(codes),
            "batches": len(batches),
            "response_records": response_records,
            "matched_schemes": matched_schemes,
            "checked": len(checked_updates),
            "updated": updated_count,
            "failed_batches": failed_batches,
            "retry_after_days": cls.RETRY_AFTER.days,
        }
        logger.info("Mutual-fund manager enrichment completed: %s", result)
        return result
