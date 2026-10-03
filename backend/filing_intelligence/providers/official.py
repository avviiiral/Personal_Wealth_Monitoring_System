import csv
import io
import logging
import os
from datetime import datetime, timedelta

import requests

from .base import NormalizedFiling


logger = logging.getLogger(__name__)

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
    "published_at": (
        "published_at",
        "broadcast_date_time",
        "BROADCAST DATE/TIME",
        "date",
        "DATE",
    ),
}


def _pick(row, field):
    for key in FIELD_ALIASES[field]:
        if key in row and row[key] not in (None, ""):
            return str(row[key]).strip()
    return ""


def _parse_datetime(value):
    value = value.strip()
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"

    try:
        return datetime.fromisoformat(value)
    except ValueError:
        pass

    for fmt in (
        "%d-%b-%Y %H:%M:%S",
        "%d-%m-%Y %H:%M:%S",
        "%d-%b-%Y",
        "%d-%m-%Y",
    ):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue

    raise ValueError(f"Unsupported filing timestamp: {value!r}")


def _rows(response):
    text = response.text
    if (
        "json" in response.headers.get("content-type", "").lower()
        or text.lstrip().startswith(("{", "["))
    ):
        payload = response.json()
        if isinstance(payload, list):
            return payload

        if isinstance(payload, dict):
            for key in ("data", "results", "rows", "items", "records"):
                if isinstance(payload.get(key), list):
                    return payload[key]

        raise ValueError("Exchange feed JSON did not contain records")

    return list(csv.DictReader(io.StringIO(text)))


class ConfiguredExchangeFilingProvider:
    """Read a deployment-configured CSV/JSON filing feed."""

    def __init__(self, exchange, url):
        self.exchange = exchange
        self.url = (url or "").strip()

    def fetch_filings(self, since):
        if not self.url:
            raise RuntimeError(
                f"{self.exchange} filing feed is not configured. "
                f"Set EXCHANGE_FILING_FEED_URL_{self.exchange} or use a built-in provider."
            )

        response = requests.get(
            self.url,
            params={"from": since.isoformat()},
            timeout=30,
            headers={
                "Accept": "application/json,text/csv;q=0.9",
                "User-Agent": "PWMS/1.0 corporate-filing-monitor",
            },
        )
        response.raise_for_status()

        for row in _rows(response):
            published = _pick(row, "published_at")
            if not published:
                continue

            dt = _parse_datetime(published)
            if dt.tzinfo is None:
                from django.utils import timezone

                dt = timezone.make_aware(dt)

            yield NormalizedFiling(
                exchange=self.exchange,
                company_name=_pick(row, "company_name"),
                symbol=_pick(row, "symbol"),
                isin=_pick(row, "isin").upper(),
                bse_code=_pick(row, "bse_code"),
                filing_type=_pick(row, "filing_type"),
                subject=_pick(row, "subject"),
                details=_pick(row, "details"),
                filing_url=_pick(row, "filing_url") or _pick(row, "source_url"),
                source_url=_pick(row, "source_url") or self.url,
                external_filing_id=_pick(row, "external_filing_id"),
                published_at=dt,
            )


class NSEFilingProvider:
    """Fetch NSE's public corporate-announcement feed.

    NSE exposes the same announcement data used by its Corporate Filings
    page through /api/corporate-announcements. The client first opens the
    public announcements page to establish the normal web session, then
    requests the JSON feed with the session's cookies. No paid API key or
    credential is required.
    """

    PAGE_URL = "https://www.nseindia.com/companies-listing/corporate-filings-announcements"
    API_URL = "https://www.nseindia.com/api/corporate-announcements"

    HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0 Safari/537.36"
        ),
        "Accept": "application/json,text/plain,*/*",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": PAGE_URL,
    }

    def __init__(self):
        self.override_url = os.environ.get("EXCHANGE_FILING_FEED_URL_NSE", "").strip()

    def fetch_filings(self, since):
        if self.override_url:
            yield from ConfiguredExchangeFilingProvider("NSE", self.override_url).fetch_filings(since)
            return

        from django.utils import timezone

        now = timezone.now()
        session = requests.Session()
        session.headers.update(self.HEADERS)

        # The public NSE API expects the normal website session/cookies.
        landing = session.get(self.PAGE_URL, timeout=30)
        landing.raise_for_status()

        params = {
            "index": "equities",
            "from_date": since.astimezone(timezone.get_current_timezone()).strftime("%d-%m-%Y"),
            "to_date": now.astimezone(timezone.get_current_timezone()).strftime("%d-%m-%Y"),
        }

        response = session.get(self.API_URL, params=params, timeout=30)
        response.raise_for_status()
        rows = _rows(response)

        logger.info("NSE corporate announcement feed returned %s rows", len(rows))

        for row in rows:
            symbol = str(row.get("symbol") or "").strip()
            company_name = str(row.get("sm_name") or row.get("companyName") or "").strip()
            subject = str(row.get("desc") or row.get("subject") or "").strip()
            details = str(row.get("attchmntText") or row.get("description") or "").strip()
            filing_url = str(
                row.get("attchmntFile")
                or row.get("attachmentURL")
                or row.get("filing_url")
                or ""
            ).strip()
            published_raw = str(
                row.get("an_dt")
                or row.get("sort_date")
                or row.get("dt")
                or ""
            ).strip()

            if not published_raw or not (symbol or company_name) or not subject:
                continue

            try:
                published_at = _parse_datetime(published_raw)
            except ValueError:
                logger.warning(
                    "Skipping NSE filing with unsupported timestamp symbol=%s value=%r",
                    symbol,
                    published_raw,
                )
                continue

            if published_at.tzinfo is None:
                published_at = timezone.make_aware(published_at)

            # Keep the exchange-provided sequence id stable across repeated runs.
            external_id = str(
                row.get("seq_id")
                or row.get("id")
                or row.get("external_filing_id")
                or ""
            ).strip()

            source_url = self.PAGE_URL
            if not filing_url:
                # NewsArticle requires a URL. Use the official NSE announcement
                # page only as a fallback; the source remains explicitly NSE.
                filing_url = source_url

            yield NormalizedFiling(
                exchange="NSE",
                company_name=company_name or symbol,
                symbol=symbol,
                isin=str(row.get("sm_isin") or row.get("isin") or "").strip().upper(),
                bse_code="",
                filing_type=str(row.get("category") or row.get("filing_type") or "").strip(),
                subject=subject,
                details=details,
                filing_url=filing_url,
                source_url=source_url,
                external_filing_id=external_id,
                published_at=published_at,
            )


class BSEFilingProvider(ConfiguredExchangeFilingProvider):
    def __init__(self):
        super().__init__("BSE", os.environ.get("EXCHANGE_FILING_FEED_URL_BSE"))
