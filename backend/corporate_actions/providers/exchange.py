"""Configured corporate-actions provider for authorised exchange data feeds.

PWMS does not scrape the NSE interactive site or bypass its access controls.
Configure an authorised/licensed HTTPS API or permitted CSV/JSON export.
"""
import csv
import io
import logging
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from urllib.parse import urlparse

import requests

logger = logging.getLogger(__name__)

ALIASES = {
    "exchange": ("exchange", "EXCHANGE"),
    "symbol": ("symbol", "SYMBOL", "nse_symbol", "NSE SYMBOL"),
    "company_name": ("company_name", "company", "companyName", "COMPANY NAME"),
    "isin": ("isin", "ISIN"),
    "action_type": ("action_type", "type", "purpose", "PURPOSE", "purpose_description"),
    "purpose": ("purpose", "PURPOSE", "description", "DESCRIPTION"),
    "face_value": ("face_value", "faceValue", "FACE VALUE"),
    "ex_date": ("ex_date", "exDate", "EX DATE", "ex-date"),
    "record_date": ("record_date", "recordDate", "RECORD DATE", "record-date"),
    "book_closure_start": ("book_closure_start", "bc_start_date", "BOOK CLOSURE START DATE"),
    "book_closure_end": ("book_closure_end", "bc_end_date", "BOOK CLOSURE END DATE"),
    "payment_date": ("payment_date", "paymentDate", "PAYMENT DATE"),
    "dividend_per_share": ("dividend_per_share", "dividend", "DIVIDEND PER SHARE"),
    "ratio": ("ratio", "entitlement_ratio", "RATIO"),
    "source_url": ("source_url", "url", "URL", "source_reference"),
    "event_id": ("event_id", "id", "EVENT ID", "corporate_action_id"),
    "status": ("status", "STATUS", "event_status"),
}

def _value(row, field):
    normalized = {str(k).strip().lower().replace(" ", "_"): v for k, v in row.items()}
    for alias in ALIASES[field]:
        key = alias.strip().lower().replace(" ", "_")
        if key in normalized and normalized[key] not in (None, ""):
            return str(normalized[key]).strip()
    return ""

def _date(value):
    if not value:
        return None
    value = value.strip()
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        pass
    for fmt in ("%d-%b-%Y", "%d %b %Y", "%d-%B-%Y", "%d %B %Y",
                "%d-%m-%Y", "%d/%m/%Y", "%d.%m.%Y"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Unsupported corporate-action date: {value!r}")

def _decimal(value):
    if not value:
        return None
    try:
        return Decimal(value.replace(",", "").replace("₹", "").strip())
    except InvalidOperation as exc:
        raise ValueError(f"Invalid corporate-action numeric value: {value!r}") from exc

def _rows(response):
    text = response.text
    if "json" in response.headers.get("content-type", "").lower() or text.lstrip().startswith(("{", "[")):
        payload = response.json()
        if isinstance(payload, list):
            return payload
        if isinstance(payload, dict):
            for key in ("data", "records", "results", "items", "corporateActions"):
                if isinstance(payload.get(key), list):
                    return payload[key]
        raise ValueError("Corporate-actions JSON feed did not contain a record list")
    return list(csv.DictReader(io.StringIO(text)))

class ConfiguredCorporateActionsProvider:
    """Fetch normalised events from a configured authorised HTTPS feed."""

    def __init__(self, url, timeout_seconds=30, max_response_bytes=10_000_000):
        self.url = (url or "").strip()
        self.timeout_seconds = int(timeout_seconds)
        self.max_response_bytes = int(max_response_bytes)

    def fetch(self, since=None, until=None):
        if not self.url:
            return []
        parsed = urlparse(self.url)
        if parsed.scheme != "https" or not parsed.hostname:
            raise ValueError("Corporate-actions feed URL must be HTTPS")
        response = requests.get(
            self.url,
            params={**({"from": since.isoformat()} if since else {}),
                    **({"to": until.isoformat()} if until else {})},
            timeout=self.timeout_seconds,
            headers={"Accept": "application/json,text/csv;q=0.9", "User-Agent": "PWMS/1.0"},
        )
        response.raise_for_status()
        declared_size = response.headers.get("Content-Length")
        if declared_size and int(declared_size) > self.max_response_bytes:
            raise ValueError("Corporate-actions feed response exceeds configured size limit")
        if len(response.content) > self.max_response_bytes:
            raise ValueError("Corporate-actions feed response exceeds configured size limit")

        records = []
        for index, row in enumerate(_rows(response)):
            exchange = _value(row, "exchange").upper()
            isin = _value(row, "isin").upper()
            if exchange not in {"NSE", "BSE"}:
                continue
            if isin and (len(isin) != 12 or not isin.isalnum()):
                logger.warning("Skipping corporate-action row %s with invalid ISIN", index)
                continue
            ex_date = _date(_value(row, "ex_date"))
            # Never substitute a record date, announcement date or book-closure date.
            if not ex_date:
                continue
            records.append({
                "exchange": exchange, "symbol": _value(row, "symbol").upper(),
                "company_name": _value(row, "company_name"), "isin": isin,
                "action_type": _value(row, "action_type"), "purpose": _value(row, "purpose"),
                "face_value": _decimal(_value(row, "face_value")), "ex_date": ex_date,
                "record_date": _date(_value(row, "record_date")),
                "book_closure_start": _date(_value(row, "book_closure_start")),
                "book_closure_end": _date(_value(row, "book_closure_end")),
                "payment_date": _date(_value(row, "payment_date")),
                "dividend_per_share": _decimal(_value(row, "dividend_per_share")),
                "ratio": _value(row, "ratio"), "source_url": _value(row, "source_url") or self.url,
                "event_id": _value(row, "event_id"), "status": _value(row, "status").upper(),
            })
        return records
