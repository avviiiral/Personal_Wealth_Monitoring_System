"""NSE's public corporate-actions endpoint, queried once per held NSE symbol.

The provider uses NSE's published JSON endpoint behind the Corporate Actions page.
It performs a normal homepage session warm-up and does not attempt to bypass
access controls. If NSE denies access, the caller records the failure.
"""
import logging
import time
from datetime import date, datetime
from urllib.parse import urlencode

import requests

logger = logging.getLogger(__name__)

NSE_HOME = "https://www.nseindia.com/"
NSE_ACTIONS_API = "https://www.nseindia.com/api/corporates-corporateActions"
NSE_ACTIONS_PAGE = "https://www.nseindia.com/companies-listing/corporate-filings-actions"


def normalize_nse_symbol(value):
    """Return the bare NSE equity symbol for values stored with an exchange suffix."""
    symbol = str(value or "").strip().upper()
    return symbol[:-3] if symbol.endswith(".NS") else symbol


def parse_nse_date(value):
    if value in (None, "", "-"):
        return None
    value = str(value).strip()
    for fmt in ("%d-%b-%Y", "%d-%B-%Y", "%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Unsupported NSE corporate-action date: {value!r}")


def normalize_nse_action(row, requested_symbol):
    symbol = str(row.get("symbol") or requested_symbol).strip().upper()
    purpose = str(row.get("subject") or row.get("purpose") or "").strip()
    return {
        "exchange": "NSE",
        "symbol": symbol,
        "company_name": str(row.get("comp") or row.get("companyName") or "").strip(),
        "series": str(row.get("series") or "").strip(),
        "purpose": purpose,
        "face_value": row.get("faceVal"),
        "ex_date": parse_nse_date(row.get("exDate")),
        "record_date": parse_nse_date(row.get("recDate")),
        "book_closure_start": parse_nse_date(row.get("bcStartDate")),
        "book_closure_end": parse_nse_date(row.get("bcEndDate")),
        "payment_date": parse_nse_date(row.get("payDate")),
        "source_url": f"{NSE_ACTIONS_PAGE}?{urlencode({'symbol': symbol})}",
        "event_id": "",
        "status": "",
        "raw": row,
    }


class NseCorporateActionsProvider:
    def __init__(self, timeout_seconds=20, max_response_bytes=5_000_000, request_delay_seconds=0.25):
        self.timeout_seconds = int(timeout_seconds)
        self.max_response_bytes = int(max_response_bytes)
        self.request_delay_seconds = max(0.0, float(request_delay_seconds))
        self.session = requests.Session()
        self._warmed = False

    def fetch_symbol(self, symbol, since, until, session=None):
        symbol = normalize_nse_symbol(symbol)
        if not symbol:
            return []
        if since and until and since > until:
            raise ValueError("NSE corporate-actions start date cannot be after end date.")

        client = session or self.session
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-IN,en;q=0.9",
            "Referer": NSE_ACTIONS_PAGE,
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36",
        }
        # NSE expects a regular browser session; this is a standard public-page
        # warm-up, not an attempt to defeat a challenge or access restriction.
        if not self._warmed:
            try:
                home = client.get(NSE_HOME, headers=headers, timeout=self.timeout_seconds)
                if home.ok:
                    self._warmed = True
                else:
                    logger.warning(
                        "NSE homepage warm-up returned HTTP %s; attempting the public corporate-actions API directly.",
                        home.status_code,
                    )
                    self._warmed = True
            except requests.RequestException as exc:
                logger.warning(
                    "NSE homepage warm-up failed (%s); attempting the public corporate-actions API directly.",
                    exc,
                )
                self._warmed = True
            if self.request_delay_seconds:
                time.sleep(self.request_delay_seconds)

        params = {"index": "equities", "symbol": symbol}
        if since:
            params["from_date"] = since.strftime("%d-%m-%Y")
        if until:
            params["to_date"] = until.strftime("%d-%m-%Y")
        response = client.get(
            NSE_ACTIONS_API,
            params=params,
            headers={**headers, "Referer": f"{NSE_ACTIONS_PAGE}?symbol={symbol}"},
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        content = response.content
        if len(content) > self.max_response_bytes:
            raise ValueError(f"NSE response for {symbol} exceeded configured size limit.")
        payload = response.json()
        if not isinstance(payload, list):
            raise ValueError(f"NSE corporate-actions response for {symbol} was not a list.")
        records = []
        for row in payload:
            if not isinstance(row, dict):
                continue
            record = normalize_nse_action(row, symbol)
            # Guard against unexpected cross-symbol records from a provider response.
            if record["symbol"] != symbol:
                logger.warning("Ignoring NSE corporate-action row for unexpected symbol %s (requested %s)", record["symbol"], symbol)
                continue
            records.append(record)
        return records
