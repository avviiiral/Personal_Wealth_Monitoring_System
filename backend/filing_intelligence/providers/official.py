import csv
import io
import os
from datetime import datetime

import requests

from .base import NormalizedFiling

FIELD_ALIASES = {
    "company_name": ("company_name", "company", "companyName", "COMPANY NAME"),
    "symbol": ("symbol", "SYMBOL", "nse_symbol", "NSE SYMBOL"),
    "isin": ("isin", "ISIN"),
    "bse_code": ("bse_code", "scrip_code", "SCRIP CODE", "security_code"),
    "filing_type": ("filing_type", "type", "TYPE"),
    "subject": ("subject", "SUBJECT", "purpose", "PURPOSE"),
    "details": ("details", "DETAILS", "description", "DESCRIPTION"),
    "filing_url": ("filing_url", "url", "URL", "attachment_url", "ATTACHMENT"),
    "source_url": ("source_url", "SOURCE URL"),
    "external_filing_id": ("external_filing_id", "id", "ID", "filing_id"),
    "published_at": ("published_at", "broadcast_date_time", "BROADCAST DATE/TIME", "date", "DATE"),
}

def _pick(row, field):
    for key in FIELD_ALIASES[field]:
        if key in row and row[key] not in (None, ""): return str(row[key]).strip()
    return ""

def _parse_datetime(value):
    value = value.strip()
    if value.endswith("Z"): value = value[:-1] + "+00:00"
    try: return datetime.fromisoformat(value)
    except ValueError: pass
    for fmt in ("%d-%b-%Y %H:%M:%S", "%d-%m-%Y %H:%M:%S", "%d-%b-%Y", "%d-%m-%Y"):
        try: return datetime.strptime(value, fmt)
        except ValueError: continue
    raise ValueError(f"Unsupported filing timestamp: {value!r}")

def _rows(response):
    text = response.text
    if "json" in response.headers.get("content-type", "").lower() or text.lstrip().startswith(("{", "[")):
        payload = response.json()
        if isinstance(payload, list): return payload
        for key in ("data", "results", "rows", "items", "records"):
            if isinstance(payload.get(key), list): return payload[key]
        raise ValueError("Exchange feed JSON did not contain records")
    return list(csv.DictReader(io.StringIO(text)))

class ConfiguredExchangeFilingProvider:
    def __init__(self, exchange, url): self.exchange, self.url = exchange, (url or "").strip()
    def fetch_filings(self, since):
        if not self.url: raise RuntimeError(f"{self.exchange} filing feed is not configured")
        response = requests.get(self.url, params={"from": since.isoformat()}, timeout=30, headers={"Accept": "application/json,text/csv;q=0.9"})
        response.raise_for_status()
        for row in _rows(response):
            published = _pick(row, "published_at")
            if not published: continue
            dt = _parse_datetime(published)
            if dt.tzinfo is None:
                from django.utils import timezone
                dt = timezone.make_aware(dt)
            yield NormalizedFiling(exchange=self.exchange, company_name=_pick(row,"company_name"), symbol=_pick(row,"symbol"), isin=_pick(row,"isin").upper(), bse_code=_pick(row,"bse_code"), filing_type=_pick(row,"filing_type"), subject=_pick(row,"subject"), details=_pick(row,"details"), filing_url=_pick(row,"filing_url") or _pick(row,"source_url"), source_url=_pick(row,"source_url") or self.url, external_filing_id=_pick(row,"external_filing_id"), published_at=dt)

class NSEFilingProvider(ConfiguredExchangeFilingProvider):
    def __init__(self): super().__init__("NSE", os.environ.get("EXCHANGE_FILING_FEED_URL_NSE"))

class BSEFilingProvider(ConfiguredExchangeFilingProvider):
    def __init__(self): super().__init__("BSE", os.environ.get("EXCHANGE_FILING_FEED_URL_BSE"))
