import logging
import threading
import time
import uuid
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation

import requests
from django.utils import timezone

from config.pwms_config import get as get_pwms_config

logger = logging.getLogger(__name__)


class BondProviderUnavailable(Exception):
    """NSE CBRICS is disabled or rejected the request; prices are unchanged."""


class BondPriceService:
    """
    Fetches the latest reported bond trade from NSE CBRICS
    using ISIN.

    The service uses the latest reported trade price available
    for the requested ISIN.

    This is intentionally ISIN-driven so new bonds can be
    discovered automatically without manual symbol mapping.
    """

    CONFIG = get_pwms_config("providers", "bond_price", {})
    ENDPOINT = CONFIG.get("endpoint", "")
    TIMEOUT = CONFIG.get("timeout_seconds", 30)
    CONNECT_TIMEOUT = CONFIG.get("connect_timeout_seconds", 10)
    ENABLED = CONFIG.get("enabled", True)
    # The endpoint returns every reported trade for the date range, not one
    # ISIN, so one download serves every bond in a refresh cycle.
    RESPONSE_CACHE_SECONDS = int(CONFIG.get("response_cache_minutes", 10)) * 60
    # After HTTP 4xx (the request itself is rejected) stop calling NSE for a
    # while instead of failing every bond on every 15-minute cycle.
    REJECTED_COOLDOWN_SECONDS = int(CONFIG.get("rejected_cooldown_hours", 24)) * 3600

    _lock = threading.Lock()
    _cached_rows = None
    _cached_key = None
    _cached_at = 0.0
    _disabled_until = 0.0
    _disabled_reason = ""

    @classmethod
    def reset_state(cls):
        with cls._lock:
            cls._cached_rows = None
            cls._cached_key = None
            cls._cached_at = 0.0
            cls._disabled_until = 0.0
            cls._disabled_reason = ""

    @classmethod
    def status(cls):
        """Human-readable provider status for logs and diagnostics."""
        if not cls.ENABLED:
            return "disabled in pwms_config.json (providers.bond_price.enabled=false)"
        if cls._disabled_until > time.monotonic():
            return cls._disabled_reason
        return "available"

    @classmethod
    def _rows(cls, from_date):
        if not cls.ENABLED:
            raise BondProviderUnavailable(
                "NSE CBRICS bond prices are disabled (providers.bond_price.enabled=false); "
                "bond prices were not changed."
            )
        with cls._lock:
            now = time.monotonic()
            if cls._disabled_until > now:
                raise BondProviderUnavailable(cls._disabled_reason)
            if (
                cls._cached_rows is not None
                and cls._cached_key == from_date
                and now - cls._cached_at < cls.RESPONSE_CACHE_SECONDS
            ):
                return cls._cached_rows

            payload = {
                "filtFromModSettleDate": from_date,
                "columnNames": cls.COLUMN_NAMES,
            }
            response = requests.post(
                cls.ENDPOINT,
                json=payload,
                headers=cls._headers(),
                timeout=(cls.CONNECT_TIMEOUT, cls.TIMEOUT),
            )
            if 400 <= response.status_code < 500:
                retry_at = timezone.localtime() + timedelta(seconds=cls.REJECTED_COOLDOWN_SECONDS)
                cls._disabled_until = now + cls.REJECTED_COOLDOWN_SECONDS
                cls._disabled_reason = (
                    f"NSE CBRICS rejected the bond price request (HTTP {response.status_code}); "
                    f"bond prices were not changed; retry after {retry_at:%Y-%m-%d %H:%M}. "
                    "Set providers.bond_price.enabled=false to stop trying."
                )
                logger.warning(cls._disabled_reason)
                raise BondProviderUnavailable(cls._disabled_reason)
            response.raise_for_status()
            data = response.json()
            rows = data if isinstance(data, list) else []
            cls._cached_rows = rows
            cls._cached_key = from_date
            cls._cached_at = now
            return rows

    COLUMN_NAMES = [
        "modRemarksBuyer",
        "symbol",
        "secPayinRemarks",
        "field4",
        "issueCouponRate",
        "issueDesc",
        "price",
        "yield",
        "yieldType",
        "refNo",
        "putCallDate",
        "value",
        "reportTime",
        "modSettleDate",
        "modRemarksSeller",
        "filtCustodian",
        "filtCounterParty",
    ]

    @classmethod
    def _headers(cls):
        return {
            "pageToken": str(uuid.uuid4()),
            "User-Agent": cls.CONFIG.get("user_agent", ""),
            "Accept": cls.CONFIG.get("accept", ""),
            "X-Requested-With": "XMLHttpRequest",
            "Origin": cls.CONFIG.get("origin", ""),
            "Referer": cls.CONFIG.get("referer", ""),
        }

    @classmethod
    def _parse_decimal(cls, value):
        if value is None:
            return None

        try:
            return Decimal(str(value))
        except (
            InvalidOperation,
            TypeError,
            ValueError,
        ):
            return None

    @classmethod
    def _parse_trade_date(cls, value):
        if not value:
            return None

        value = str(value).strip()

        formats = [
            "%d-%m-%Y %H:%M:%S",
            "%d-%m-%Y %H:%M",
            "%d-%m-%Y",
        ]

        for fmt in formats:
            try:
                return datetime.strptime(
                    value,
                    fmt,
                ).date()
            except ValueError:
                continue

        return None

    @classmethod
    def get_latest_price(cls, isin):
        """
        Return the latest NSE-reported trade for an ISIN.

        Returns:

        {
            "isin": "...",
            "price": Decimal(...),
            "date": date(...),
            "yield": Decimal(...),
            "trade_value": Decimal(...),
            "issuer": "...",
            "description": "..."
        }

        Returns None when no trade is available.
        """

        if not isin:
            return None

        normalized_isin = (
            str(isin)
            .strip()
            .upper()
        )

        today = timezone.localdate()

        from_date = (
            today - timedelta(days=6)
        ).strftime("%d-%m-%Y")

        data = cls._rows(from_date)

        matches = []

        for row in data:

            if not isinstance(row, list):
                continue

            if len(row) <= 13:
                continue

            row_isin = str(
                row[1] or ""
            ).strip().upper()

            if row_isin != normalized_isin:
                continue

            price = cls._parse_decimal(
                row[6]
            )

            if price is None:
                continue

            trade_date = (
                cls._parse_trade_date(
                    row[12]
                )
                or cls._parse_trade_date(
                    row[13]
                )
            )

            if trade_date is None:
                continue

            matches.append(
                {
                    "isin": normalized_isin,
                    "price": price,
                    "date": trade_date,
                    "yield": cls._parse_decimal(
                        row[7]
                    ),
                    "trade_value": (
                        cls._parse_decimal(
                            row[11]
                        )
                    ),
                    "issuer": (
                        str(row[3] or "").strip()
                    ),
                    "description": (
                        str(row[5] or "").strip()
                    ),
                }
            )

        if not matches:
            return None

        matches.sort(
            key=lambda item: item["date"],
            reverse=True,
        )

        return matches[0]