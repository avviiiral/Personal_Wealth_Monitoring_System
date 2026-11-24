"""Large / Mid / Small cap classification of equity ISINs.

Primary source: AMFI's semi-annual "Average Market Capitalisation of
listed companies" workbook (SEBI circular of 6 Oct 2017: rank 1-100 Large,
101-250 Mid, 251+ Small). AMFI publishes it after each half-year at
portal.amfiindia.com/spages/AverageMarketCapitalization<DD><Mon><YYYY>.xlsx.

Fallback: when the workbook cannot be downloaded, ISINs held by funds
tracking the NIFTY 100 are treated as Large Cap and those held by funds
tracking the NIFTY Midcap 150 as Mid Cap. That is an index-membership
proxy, stored with source INDEX_PROXY so the UI can say so, and it never
overwrites AMFI rows.
"""

import io
import logging
import re
from datetime import date
from decimal import Decimal

import requests
from django.db import transaction
from django.db.models import F
from openpyxl import load_workbook

from config.database_scheduler_lock import DATABASE_SCHEDULER_LOCK
from config.pwms_config import get as get_pwms_config
from mf_intelligence.models import MFScheme, MFStockClassification


logger = logging.getLogger(__name__)

AMFI_URL_TEMPLATE = "https://portal.amfiindia.com/spages/AverageMarketCapitalization{day}{month}{year}.xlsx"
_CAP_LABELS = {"large cap": "Large Cap", "mid cap": "Mid Cap", "small cap": "Small Cap"}
_PERIOD_IN_TITLE = re.compile(r"(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})")
_MONTHS = {name: index for index, name in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1
)}


def candidate_periods(today=None, count=3):
    """Most recent half-year ends first: 31 Dec and 30 Jun."""
    today = today or date.today()
    periods = []
    year = today.year
    for _ in range(count + 2):
        for period in (date(year, 12, 31), date(year, 6, 30)):
            # AMFI publishes in early January / early July.
            if period < today:
                periods.append(period)
        year -= 1
    periods = sorted(set(periods), reverse=True)
    return periods[:count]


def amfi_url_for(period):
    template = get_pwms_config("mf_intelligence", "amfi_cap_list_url_template", AMFI_URL_TEMPLATE)
    return template.format(day=f"{period.day:02d}", month=period.strftime("%b"), year=period.year)


def _normalise(value):
    return " ".join(str(value or "").strip().lower().split())


def parse_amfi_workbook(content):
    """Parse the AMFI workbook bytes into (period_end, rows).

    Columns are located by header text, not position, because AMFI has
    reshuffled them between periods.
    """
    workbook = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    sheet = workbook.worksheets[0]
    period_end = None
    header_index = None
    columns = {}
    rows = []
    for row_number, row in enumerate(sheet.iter_rows(values_only=True)):
        if header_index is None:
            texts = [_normalise(cell) for cell in row]
            joined = " ".join(texts)
            if period_end is None:
                match = _PERIOD_IN_TITLE.search(" ".join(str(cell or "") for cell in row))
                if match and "market capitali" in joined:
                    day, month_name, year = match.groups()
                    month = _MONTHS.get(month_name[:3].lower())
                    if month:
                        try:
                            period_end = date(int(year), month, int(day))
                        except ValueError:
                            period_end = None
            if "isin" in texts:
                header_index = row_number
                for index, text in enumerate(texts):
                    if text == "isin":
                        columns["isin"] = index
                    elif text.startswith("sr") and "no" in text:
                        columns["rank"] = index
                    elif "company" in text and "name" in text:
                        columns["company"] = index
                    elif text.startswith("average of all"):
                        columns["avg_cap"] = index
                    elif text.startswith("categori"):
                        columns["bucket"] = index
            if row_number > 30 and header_index is None:
                break
            continue
        if "isin" not in columns:
            break
        isin = str(row[columns["isin"]] or "").strip().upper()
        if not re.match(r"^[A-Z]{2}[A-Z0-9]{10}$", isin):
            continue
        rank = row[columns["rank"]] if "rank" in columns else None
        try:
            rank = int(rank) if rank is not None else None
        except (TypeError, ValueError):
            rank = None
        bucket = None
        if "bucket" in columns:
            bucket = _CAP_LABELS.get(_normalise(row[columns["bucket"]]))
        if bucket is None and rank is not None:
            bucket = "Large Cap" if rank <= 100 else "Mid Cap" if rank <= 250 else "Small Cap"
        if bucket is None:
            continue
        avg_cap = row[columns["avg_cap"]] if "avg_cap" in columns else None
        try:
            avg_cap = Decimal(str(avg_cap)).quantize(Decimal("0.0001")) if avg_cap not in (None, "", "-") else None
        except Exception:  # noqa: BLE001 - AMFI cells occasionally hold text
            avg_cap = None
        rows.append(
            {
                "isin": isin,
                "company_name": str(row[columns["company"]] or "").strip()[:300] if "company" in columns else "",
                "rank": rank,
                "cap_bucket": bucket,
                "avg_market_cap_crore": avg_cap,
            }
        )
    workbook.close()
    return period_end, rows


def store_amfi_rows(rows, period_end):
    """Replace AMFI classifications with ``rows``; returns the row count."""
    if not rows:
        return 0
    by_isin = {row["isin"]: row for row in rows}
    with DATABASE_SCHEDULER_LOCK, transaction.atomic():
        existing = {item.isin: item for item in MFStockClassification.objects.filter(isin__in=by_isin)}
        to_create, to_update = [], []
        for isin, row in by_isin.items():
            item = existing.get(isin) or MFStockClassification(isin=isin)
            item.company_name = row["company_name"]
            item.cap_bucket = row["cap_bucket"]
            item.rank = row["rank"]
            item.avg_market_cap_crore = row["avg_market_cap_crore"]
            item.source = MFStockClassification.SOURCE_AMFI
            item.period_end = period_end
            (to_update if item.pk else to_create).append(item)
        MFStockClassification.objects.bulk_create(to_create, batch_size=1000)
        MFStockClassification.objects.bulk_update(
            to_update,
            ["company_name", "cap_bucket", "rank", "avg_market_cap_crore", "source", "period_end"],
            batch_size=1000,
        )
        # ISINs no longer on the latest AMFI list keep no stale bucket.
        MFStockClassification.objects.filter(source=MFStockClassification.SOURCE_AMFI).exclude(
            isin__in=list(by_isin)
        ).delete()
    return len(by_isin)


def latest_amfi_period():
    item = (
        MFStockClassification.objects.filter(source=MFStockClassification.SOURCE_AMFI)
        .exclude(period_end__isnull=True)
        .order_by("-period_end")
        .first()
    )
    return item.period_end if item else None


def refresh_amfi_classification(today=None, force=False, http_get=None):
    """Download the newest AMFI workbook not yet stored.

    Returns a dict describing the outcome; never raises for network errors.
    """
    http_get = http_get or (lambda url: requests.get(
        url,
        timeout=60,
        headers={"User-Agent": "Mozilla/5.0 (compatible; PWMS-MFIntelligence/1.0)"},
    ))
    stored_period = latest_amfi_period()
    for period in candidate_periods(today):
        if stored_period and period <= stored_period and not force:
            return {"status": "current", "period_end": str(stored_period)}
        url = amfi_url_for(period)
        response = None
        for attempt in range(2):  # AMFI occasionally resets the first connection
            try:
                response = http_get(url)
                break
            except requests.RequestException as exc:
                logger.warning("AMFI cap list download failed for %s (attempt %s): %s", url, attempt + 1, exc)
        if response is None:
            continue
        content_type = response.headers.get("Content-Type", "")
        if response.status_code != 200 or "html" in content_type.lower():
            continue
        try:
            parsed_period, rows = parse_amfi_workbook(response.content)
        except Exception as exc:  # noqa: BLE001 - a malformed workbook must not stop the sync
            logger.warning("AMFI cap list at %s could not be parsed: %s", url, exc)
            continue
        if not rows:
            continue
        count = store_amfi_rows(rows, parsed_period or period)
        return {"status": "imported", "period_end": str(parsed_period or period), "rows": count, "url": url}
    if stored_period:
        return {"status": "current", "period_end": str(stored_period)}
    return {"status": "unavailable"}


_NIFTY_100 = re.compile(r"nifty\s*100\b", re.IGNORECASE)
_MIDCAP_150 = re.compile(r"mid\s*cap\s*150", re.IGNORECASE)
_EXCLUDE_FACTOR = re.compile(r"low\s*vol|quality|equal|alpha|esg|value|momentum|enhanced|30|fof|fund of fund", re.IGNORECASE)


def proxy_tracker_codes(limit_per_index=2):
    """AMFI codes of a few index funds/ETFs tracking NIFTY 100 / Midcap 150."""
    trackers = {"Large Cap": [], "Mid Cap": []}
    candidates = (
        MFScheme.objects.filter(
            is_active=True,
            category__slug__in=["index-broad-market", "etf-broad-market"],
            option__in=["Growth", "Other"],
        )
        .exclude(plan="Direct")
        .order_by(F("avg_aum_crore").desc(nulls_last=True), "amfi_code")
        .values_list("amfi_code", "name")
    )
    for code, name in candidates:
        if _EXCLUDE_FACTOR.search(name):
            continue
        if _MIDCAP_150.search(name) and len(trackers["Mid Cap"]) < limit_per_index:
            trackers["Mid Cap"].append(code)
        elif _NIFTY_100.search(name) and len(trackers["Large Cap"]) < limit_per_index:
            trackers["Large Cap"].append(code)
    return trackers


def build_index_proxy(client):
    """Store INDEX_PROXY buckets from tracker fund holdings."""
    trackers = proxy_tracker_codes()
    members = {}
    for bucket in ("Mid Cap", "Large Cap"):  # Large wins on overlap
        for code in trackers[bucket]:
            try:
                payload = client.holdings(code, change=False)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Index proxy holdings fetch failed for %s: %s", code, exc)
                continue
            for holding in payload.get("holdings") or []:
                isin = (holding.get("isin") or "").strip().upper()
                if isin and (holding.get("holding_type") or "").lower() == "equity":
                    members[isin] = (bucket, (holding.get("name") or "")[:300])
    if not members:
        return {"status": "unavailable", "trackers": trackers}
    with DATABASE_SCHEDULER_LOCK, transaction.atomic():
        amfi_isins = set(
            MFStockClassification.objects.filter(source=MFStockClassification.SOURCE_AMFI).values_list("isin", flat=True)
        )
        MFStockClassification.objects.filter(source=MFStockClassification.SOURCE_INDEX_PROXY).delete()
        rows = [
            MFStockClassification(
                isin=isin,
                company_name=name,
                cap_bucket=bucket,
                source=MFStockClassification.SOURCE_INDEX_PROXY,
                period_end=date.today(),
            )
            for isin, (bucket, name) in members.items()
            if isin not in amfi_isins
        ]
        MFStockClassification.objects.bulk_create(rows, batch_size=1000)
    return {"status": "built", "rows": len(rows), "trackers": trackers}


def classification_lookup():
    """isin -> (cap_bucket, source) for every stored classification."""
    return {
        isin: (bucket, source)
        for isin, bucket, source in MFStockClassification.objects.values_list("isin", "cap_bucket", "source")
    }


def classification_meta():
    amfi_period = latest_amfi_period()
    proxy_count = MFStockClassification.objects.filter(source=MFStockClassification.SOURCE_INDEX_PROXY).count()
    return {
        "amfi_period_end": str(amfi_period) if amfi_period else None,
        "amfi_rows": MFStockClassification.objects.filter(source=MFStockClassification.SOURCE_AMFI).count(),
        "index_proxy_rows": proxy_count,
        "method": "AMFI" if amfi_period else ("INDEX_PROXY" if proxy_count else None),
    }
