import uuid
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation

import requests
from django.utils import timezone

from config.pwms_config import get as get_pwms_config


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

        payload = {
            "filtFromModSettleDate": from_date,
            "columnNames": cls.COLUMN_NAMES,
        }

        response = requests.post(
            cls.ENDPOINT,
            json=payload,
            headers=cls._headers(),
            timeout=cls.TIMEOUT,
        )

        response.raise_for_status()

        data = response.json()

        if not isinstance(data, list):
            return None

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