"""MF Forecast holdings API adapter.

Live responses may include a nested provider snapshot with an as-of date.
That date is used when available; otherwise holdings remain retrieval-date
snapshots. Provider-specific units and classifications are quality-flagged.
"""
from __future__ import annotations

import json
import threading
import time
from decimal import Decimal
import re
from urllib.parse import quote, urlparse, urlunparse

import requests
from django.utils import timezone

from mutual_funds.services.masterdata import (
    USER_AGENT,
    _instrument_type,
    decimal_value,
    normalize_isin,
    normalize_text,
    parse_date,
    source_raw_directory,
)

_HOST_LOCK = threading.Lock()
_HOST_LAST_REQUEST: dict[str, float] = {}


def normalize_mfforecast_base_url(base_url: str) -> str:
    """Accept an API base URL or strip the documented scheme endpoint suffix."""
    parsed = urlparse((base_url or "").strip())
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValueError("MF Forecast API base URL must be an absolute HTTPS URL.")
    path = parsed.path.rstrip("/")
    if re.fullmatch(r"/funds/[^/]+/holdings", path, flags=re.IGNORECASE):
        path = ""
    return urlunparse((parsed.scheme, parsed.netloc, path, "", "", "")).rstrip("/")


def build_mfforecast_headers(*, base_url: str, api_key: str, api_key_header: str = "") -> dict:
    """Build safe request headers, including both mandatory RapidAPI headers."""
    parsed = urlparse(base_url)
    host = parsed.netloc.casefold()
    key = (api_key or "").strip()
    header_name = (api_key_header or "").strip()

    if ":" in header_name or any(ch.isspace() for ch in header_name):
        raise ValueError(
            "MFFORECAST_API_KEY_HEADER must contain only a header name, not a full header line. "
            "For RapidAPI set it to x-rapidapi-key; the X-RapidAPI-Host header is added automatically."
        )

    if host.endswith(".p.rapidapi.com"):
        if not key:
            raise ValueError("MFFORECAST_API_KEY is required when using a RapidAPI host.")
        if header_name and header_name.casefold() != "x-rapidapi-key":
            raise ValueError(
                "RapidAPI requires MFFORECAST_API_KEY_HEADER=x-rapidapi-key. "
                "X-RapidAPI-Host is generated from MFFORECAST_API_BASE_URL."
            )
        return {
            "X-RapidAPI-Key": key,
            "X-RapidAPI-Host": parsed.netloc,
        }

    if bool(key) != bool(header_name):
        raise ValueError(
            "Set both MFFORECAST_API_KEY and MFFORECAST_API_KEY_HEADER, "
            "or leave both blank. The header name must come from provider documentation."
        )
    if header_name and not re.fullmatch(r"[!#$%&'*+.^_|~0-9A-Za-z-]+", header_name):
        raise ValueError("MFFORECAST_API_KEY_HEADER is not a valid HTTP header name.")
    return {header_name: key} if key else {}


# NSE corporate filing identifies this exact provider-mislabelled ISIN as an NCD.
MFFORECAST_REIT_MISLABELLED_DEBT_ISINS = {"INE296A07TN6"}


def _mfforecast_instrument_type(
    raw_type: str,
    security_name: str,
    isin: str = "",
) -> tuple[str, bool]:
    """Correct clear InvIT and debt labels supplied incorrectly as REIT.

    A plain REIT label remains REIT. Known mislabelled debt ISINs take
    precedence, followed by identifiable infrastructure trusts and
    coupon-bearing or maturity-dated debt instruments.
    """
    provider_type = normalize_text(raw_type).casefold()
    if provider_type in {"reit", "real estate investment trust"}:
        if normalize_isin(isin) in MFFORECAST_REIT_MISLABELLED_DEBT_ISINS:
            return "BOND", True
        # The provider has returned "REIT" for Indus Infra Trust. Explicit
        # infrastructure-trust names are InvIT units, not real-estate REITs.
        has_invit_identity = bool(
            re.search(
                r"\b(?:invit|infra(?:structure)?\s+(?:investment\s+)?trust)\b",
                normalize_text(security_name),
                flags=re.IGNORECASE,
            )
        )
        if has_invit_identity:
            return "INVIT", True

        has_coupon = bool(re.search(r"\b\d{1,2}(?:\.\d+)?\s*%", security_name))
        has_maturity = bool(
            re.search(r"\(\s*\d{1,2}[/-]\d{1,2}[/-]\d{4}\s*\)", security_name)
        )
        if has_coupon or has_maturity:
            return "BOND", True
    return _instrument_type(raw_type, security_name), False


def parse_mfforecast_holdings(payload: dict, *, scheme_code: str) -> dict:
    """Validate and normalize MF Forecast holdings and nested snapshot metadata."""
    if not isinstance(payload, dict):
        raise ValueError("MF Forecast response must be a JSON object.")
    if "holdings" not in payload or not isinstance(payload.get("holdings"), list):
        raise ValueError("MF Forecast response must include a holdings array.")

    provider_snapshot = payload.get("snapshot")
    if not isinstance(provider_snapshot, dict):
        provider_snapshot = {}

    allocation_raw = payload.get("asset_allocation")
    if not allocation_raw:
        allocation_raw = provider_snapshot
    if allocation_raw is None:
        allocation_raw = {}
    if not isinstance(allocation_raw, dict):
        raise ValueError("MF Forecast asset_allocation must be a JSON object.")

    asset_allocation = {}
    for field in ("equity_pct", "debt_pct", "cash_pct"):
        value = decimal_value(allocation_raw.get(field), percent=True)
        if value is not None and value >= 0:
            asset_allocation[field] = str(value)

    provider_count_raw = payload.get("holdings_count")
    try:
        provider_holdings_count = int(provider_count_raw) if provider_count_raw is not None else 0
    except (TypeError, ValueError, OverflowError):
        provider_holdings_count = 0
    if provider_holdings_count < 0:
        provider_holdings_count = 0

    raw_holdings = payload["holdings"]
    rows = []
    invalid_rows = 0
    missing_weight_rows = 0
    provider_asset_type_overrides = 0
    for item in raw_holdings:
        if not isinstance(item, dict):
            invalid_rows += 1
            continue

        # The live RapidAPI response uses "name"; the published example uses
        # "stock_name". Keep named rows with missing weights out of holdings,
        # but track them separately so weight units can be inferred safely from
        # the remaining complete security records.
        security_name = normalize_text(item.get("name") or item.get("stock_name"))
        raw_weight = item.get("weight_pct")
        weight = decimal_value(raw_weight, percent=True)
        if not security_name:
            invalid_rows += 1
            continue
        if weight is None or weight < 0:
            invalid_rows += 1
            if raw_weight is None or (
                isinstance(raw_weight, str) and not raw_weight.strip()
            ):
                missing_weight_rows += 1
            continue

        raw_type = normalize_text(item.get("asset_type"))
        isin = normalize_isin(item.get("isin"))
        instrument_type, type_overridden = _mfforecast_instrument_type(
            raw_type, security_name, isin,
        )
        provider_asset_type_overrides += int(type_overridden)
        rows.append({
            "security_name": security_name[:300],
            "ticker": normalize_text(item.get("ticker"))[:40],
            "isin": isin,
            "quantity": decimal_value(item.get("quantity")),
            "pct_of_nav": weight,
            "instrument_type": instrument_type,
        })

    raw_total_weight = sum((row["pct_of_nav"] for row in rows), Decimal("0"))
    weight_scale_factor = Decimal("1")
    # Complete provider responses can be scaled as before. Also allow the same
    # inference when the provider count matches the raw array and every skipped
    # row is a named security with a genuinely missing weight. Other malformed
    # rows, invalid names, negative weights, and incomplete raw arrays block
    # inference. Missing weights are never synthesized or persisted.
    complete_response = (
        bool(rows)
        and invalid_rows == 0
        and provider_holdings_count == len(rows)
    )
    missing_weights_only_response = (
        bool(rows)
        and missing_weight_rows > 0
        and missing_weight_rows == invalid_rows
        and provider_holdings_count == len(raw_holdings)
        and len(raw_holdings) == len(rows) + missing_weight_rows
    )
    if (
        (complete_response or missing_weights_only_response)
        and Decimal("0") < raw_total_weight <= Decimal("1.05")
    ):
        weight_scale_factor = Decimal("100")
        for row in rows:
            row["pct_of_nav"] = (row["pct_of_nav"] * weight_scale_factor).quantize(Decimal("0.0001"))

    total_weight = sum((row["pct_of_nav"] for row in rows), Decimal("0"))
    snapshot_date_raw = (
        provider_snapshot.get("snapshot_date")
        or payload.get("snapshot_date")
        or payload.get("as_of_date")
    )
    provider_as_of_date = parse_date(snapshot_date_raw)
    quality_flags = []
    if not rows:
        quality_flags.append("MFFORECAST_NO_VALID_HOLDING_ROWS")
    if invalid_rows:
        quality_flags.append("MFFORECAST_INVALID_HOLDING_ROWS_SKIPPED")
    if provider_holdings_count and provider_holdings_count != len(rows):
        quality_flags.append("MFFORECAST_PROVIDER_HOLDINGS_COUNT_MISMATCH")
    if not asset_allocation:
        quality_flags.append("MFFORECAST_ASSET_ALLOCATION_NOT_PROVIDED")
    elif len(asset_allocation) < 3:
        quality_flags.append("MFFORECAST_ASSET_ALLOCATION_INCOMPLETE")
    if provider_asset_type_overrides:
        quality_flags.append("MFFORECAST_PROVIDER_ASSET_TYPE_OVERRIDDEN")
    if weight_scale_factor != Decimal("1"):
        quality_flags.append("MFFORECAST_WEIGHT_SCALE_INFERRED_FRACTION_TO_PERCENT")
        if missing_weight_rows:
            quality_flags.append("MFFORECAST_WEIGHT_SCALE_INFERRED_WITH_MISSING_WEIGHT_ROWS")
    if snapshot_date_raw and provider_as_of_date is None:
        quality_flags.append("MFFORECAST_SNAPSHOT_DATE_INVALID")
    if rows and abs(total_weight - Decimal("100")) > Decimal("1.0"):
        quality_flags.append("MFFORECAST_HOLDING_WEIGHTS_NOT_100")

    aum_raw = payload.get("aum_cr")
    if aum_raw is None:
        aum_raw = provider_snapshot.get("aum_crores")
    aum_cr = decimal_value(aum_raw)
    if aum_cr is not None and aum_cr < 0:
        aum_cr = None
    expense_raw = payload.get("expense_ratio")
    if expense_raw is None:
        expense_raw = provider_snapshot.get("expense_ratio")
    expense_ratio_pct = decimal_value(expense_raw, percent=True)
    if expense_ratio_pct is not None and expense_ratio_pct < 0:
        expense_ratio_pct = None

    response_scheme_code = normalize_text(payload.get("amfi_code"))
    if response_scheme_code and response_scheme_code != str(scheme_code):
        raise ValueError(
            f"MF Forecast response AMFI code {response_scheme_code} does not match requested scheme code {scheme_code}."
        )

    return {
        "scheme_code": str(scheme_code),
        "provider_holdings_count": provider_holdings_count,
        "provider_snapshot": provider_snapshot,
        "provider_as_of_date": provider_as_of_date,
        "scheme_name": normalize_text(payload.get("scheme_name"))[:300],
        "fund_house": normalize_text(payload.get("fund_house"))[:200],
        "category": normalize_text(payload.get("category"))[:160],
        "aum_cr": aum_cr,
        "expense_ratio_pct": expense_ratio_pct,
        "asset_allocation": asset_allocation,
        "holdings": rows,
        "holdings_count": len(rows),
        "total_weight_pct": total_weight.quantize(Decimal("0.0001")),
        "weight_scale_factor": weight_scale_factor,
        "provider_asset_type_overrides": provider_asset_type_overrides,
        "quality_flags": quality_flags,
        "invalid_rows": invalid_rows,
        "missing_weight_rows": missing_weight_rows,
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
            response = session.get(
                endpoint, headers=request_headers, timeout=timeout,
                allow_redirects=False,
            )
        except requests.RequestException as exc:
            last_error = exc
            if attempt < 2:
                time.sleep(2 ** (attempt + 1))
                continue
            raise RuntimeError(f"MF Forecast request failed for scheme {scheme_code}: {exc}") from exc

        if response.is_redirect or response.is_permanent_redirect:
            raise ValueError(
                "MF Forecast endpoint redirected the request; configure the canonical HTTPS API URL. "
                "Redirects are disabled to prevent custom API-key headers being forwarded to another host."
            )
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
