"""MF Forecast holdings API adapter.

The endpoint example documents scheme-level metadata and holdings, but no
portfolio disclosure date. These observations are therefore timestamped as
retrieval-date snapshots unless a dated disclosure field is later documented.
"""
from __future__ import annotations

import json
import threading
import time
from decimal import Decimal
from pathlib import Path
from urllib.parse import quote, urlparse

import requests
from django.utils import timezone

from mutual_funds.services.masterdata import (
    USER_AGENT,
    _instrument_type,
    decimal_value,
    normalize_isin,
    normalize_text,
    source_raw_directory,
)

_HOST_LOCK = threading.Lock()
_HOST_LAST_REQUEST: dict[str, float] = {}


def parse_mfforecast_holdings(payload: dict, *, scheme_code: str) -> dict:
    """Validate and normalize the documented MF Forecast JSON response."""
    if not isinstance(payload, dict):
        raise ValueError("MF Forecast response must be a JSON object.")
    if "holdings" not in payload or not isinstance(payload.get("holdings"), list):
        raise ValueError("MF Forecast response must include a holdings array.")

    allocation_raw = payload.get("asset_allocation")
    if allocation_raw is None:
        allocation_raw = {}
    if not isinstance(allocation_raw, dict):
        raise ValueError("MF Forecast asset_allocation must be a JSON object.")

    asset_allocation = {}
    for field in ("equity_pct", "debt_pct", "cash_pct"):
        value = decimal_value(allocation_raw.get(field), percent=True)
        if value is not None and value >= 0:
            asset_allocation[field] = str(value)

    rows = []
    invalid_rows = 0
    for item in payload["holdings"]:
        if not isinstance(item, dict):
            invalid_rows += 1
            continue
        security_name = normalize_text(item.get("stock_name"))
        weight = decimal_value(item.get("weight_pct"), percent=True)
        if not security_name or weight is None or weight < 0:
            invalid_rows += 1
            continue

        raw_type = normalize_text(item.get("asset_type"))
        rows.append({
            "security_name": security_name[:300],
            "ticker": normalize_text(item.get("ticker"))[:40],
            "isin": normalize_isin(item.get("isin")),
            "quantity": decimal_value(item.get("quantity")),
            "pct_of_nav": weight,
            "instrument_type": _instrument_type(raw_type, security_name),
        })

    total_weight = sum((row["pct_of_nav"] for row in rows), Decimal("0"))
    quality_flags = []
    if not rows:
        quality_flags.append("MFFORECAST_HOLDINGS_EMPTY")
    if invalid_rows:
        quality_flags.append("MFFORECAST_INVALID_HOLDING_ROWS_SKIPPED")
    if not asset_allocation:
        quality_flags.append("MFFORECAST_ASSET_ALLOCATION_NOT_PROVIDED")
    if rows and abs(total_weight - Decimal("100")) > Decimal("1.0"):
        quality_flags.append("MFFORECAST_HOLDING_WEIGHTS_NOT_100")

    aum_cr = decimal_value(payload.get("aum_cr"))
    if aum_cr is not None and aum_cr < 0:
        aum_cr = None
    expense_ratio_pct = decimal_value(payload.get("expense_ratio"), percent=True)
    if expense_ratio_pct is not None and expense_ratio_pct < 0:
        expense_ratio_pct = None

    return {
        "scheme_code": str(scheme_code),
        "scheme_name": normalize_text(payload.get("scheme_name"))[:300],
        "fund_house": normalize_text(payload.get("fund_house"))[:200],
        "category": normalize_text(payload.get("category"))[:160],
        "aum_cr": aum_cr,
        "expense_ratio_pct": expense_ratio_pct,
        "asset_allocation": asset_allocation,
        "holdings": rows,
        "holdings_count": len(rows),
        "total_weight_pct": total_weight.quantize(Decimal("0.0001")),
        "quality_flags": quality_flags,
        "invalid_rows": invalid_rows,
    }


def _throttle(host: str) -> None:
    """Apply a conservative one-request-per-second process-level host throttle."""
    with _HOST_LOCK:
        wait = 1.0 - (time.monotonic() - _HOST_LAST_REQUEST.get(host, 0.0))
        if wait > 0:
            time.sleep(wait)
        _HOST_LAST_REQUEST[host] = time.monotonic()


def fetch_mfforecast_payload(
    *,
    session: requests.Session,
    base_url: str,
    scheme_code: str,
    headers: dict | None = None,
    timeout: int = 20,
):
    """Fetch one scheme response, retry transient failures, and cache raw JSON.

    Returns (payload, endpoint_url, raw_file_path). A 404 means no provider
    record for this AMFI scheme and returns (None, endpoint_url, "").
    """
    endpoint = f"{base_url.rstrip('/')}/funds/{quote(str(scheme_code), safe='')}/holdings"
    parsed = urlparse(endpoint)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValueError("MF Forecast API endpoint must use HTTPS.")
    request_headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
    }
    request_headers.update(headers or {})
    last_error = None

    for attempt in range(3):
        _throttle(parsed.netloc.casefold())
        try:
            response = session.get(endpoint, headers=request_headers, timeout=timeout)
        except requests.RequestException as exc:
            last_error = exc
            if attempt < 2:
                time.sleep(2 ** (attempt + 1))
                continue
            raise RuntimeError(f"MF Forecast request failed for scheme {scheme_code}: {exc}") from exc

        if response.status_code == 404:
            return None, endpoint, ""
        if response.status_code in (401, 403):
            raise PermissionError(
                f"MF Forecast rejected access for scheme {scheme_code} "
                f"(HTTP {response.status_code}); verify the provider's authentication instructions."
            )
        if response.status_code == 429:
            if attempt == 2:
                raise RuntimeError("MF Forecast rate limit (HTTP 429) persisted after retries.")
            retry_after = response.headers.get("Retry-After", "")
            try:
                delay = min(30.0, max(1.0, float(retry_after)))
            except (TypeError, ValueError):
                delay = float(2 ** (attempt + 1))
            time.sleep(delay)
            continue
        if 500 <= response.status_code <= 599:
            last_error = RuntimeError(f"MF Forecast server returned HTTP {response.status_code}.")
            if attempt < 2:
                time.sleep(2 ** (attempt + 1))
                continue
            raise last_error
        response.raise_for_status()
        try:
            payload = response.json()
        except ValueError as exc:
            raise ValueError(f"MF Forecast returned invalid JSON for scheme {scheme_code}.") from exc

        if not isinstance(payload, dict):
            raise ValueError("MF Forecast response must be a JSON object.")

        observed_on = timezone.localdate()
        raw_dir = source_raw_directory("mfforecast_api", observed_on)
        filename = f"scheme_{scheme_code}_{timezone.now().strftime('%H%M%S%f')}.json"
        raw_path = raw_dir / filename
        raw_bytes = getattr(response, "content", b"") or json.dumps(
            payload, ensure_ascii=False, indent=2,
        ).encode("utf-8")
        temp_path = raw_path.with_suffix(".json.tmp")
        temp_path.write_bytes(raw_bytes)
        temp_path.replace(raw_path)
        return payload, endpoint, str(raw_path)

    raise RuntimeError(f"Unable to fetch MF Forecast scheme {scheme_code}: {last_error}")
