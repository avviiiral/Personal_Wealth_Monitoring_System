"""SEBI-category mutual-fund universe, NAV research and peer analytics.

The pipeline is deliberately independent of IndianAPI. AMFI supplies scheme
identity and SEBI category metadata; MFapi.in supplies cached historical NAV
series. No IndianAPI credit is used by these functions.
"""
from __future__ import annotations

from bisect import bisect_right
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from itertools import combinations
import json
import logging
import math
import re
import threading
import time
from pathlib import Path
from typing import Any

import pandas as pd
import requests
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from openpyxl import load_workbook

from mutual_funds.models import AMFIMasterNAV, AMFIMasterScheme


logger = logging.getLogger("mutual_funds.sebi_analytics")

AMFI_NAVALL_URL = "https://www.amfiindia.com/spages/NAVAll.txt"
MFAPI_BASE_URL = "https://api.mfapi.in/mf"
MFAPI_SOURCE = "mfapi.in"
DEFAULT_RISK_FREE_RATE = 0.065
TRADING_DAYS_PER_YEAR = 252
DEFAULT_REQUESTS_PER_SECOND = 1.0
DEFAULT_WORKERS = 4

# Current taxonomy follows SEBI Circular HO/24/13/15(2)2026-IMD-RAC4/I/5764/2026
# dated 26-Feb-2026. Solution Oriented schemes are retained only as a
# discontinued legacy branch for old feed labels and historical comparability.
CATEGORY_TREE = {
    # 13 Equity categories from SEBI's 26-Feb-2026 circular.
    "Multi Cap": ("Equity", "By Market Cap"),
    "Large Cap": ("Equity", "By Market Cap"),
    "Large & Mid Cap": ("Equity", "By Market Cap"),
    "Mid Cap": ("Equity", "By Market Cap"),
    "Small Cap": ("Equity", "By Market Cap"),
    "Flexi Cap": ("Equity", "By Market Cap"),
    "Dividend Yield": ("Equity", "By Strategy"),
    "Value": ("Equity", "By Strategy"),
    "Contra": ("Equity", "By Strategy"),
    "Focused": ("Equity", "By Strategy"),
    "Sectoral": ("Equity", "By Sector & Theme"),
    "Thematic": ("Equity", "By Sector & Theme"),
    "ELSS - Tax Saver": ("Equity", "Tax Saving"),
    # 17 Debt categories.
    "Overnight": ("Debt", "By Duration"),
    "Liquid": ("Debt", "By Duration"),
    "Ultra Short Term": ("Debt", "By Duration"),
    "Ultra Short to Short Term": ("Debt", "By Duration"),
    "Money Market": ("Debt", "By Strategy"),
    "Short Term": ("Debt", "By Duration"),
    "Medium Term": ("Debt", "By Duration"),
    "Medium to Long Term": ("Debt", "By Duration"),
    "Long Term": ("Debt", "By Duration"),
    "Dynamic Term": ("Debt", "By Duration"),
    "Corporate Bond": ("Debt", "By Credit"),
    "Credit Risk": ("Debt", "By Credit"),
    "Banking and PSU Debt": ("Debt", "By Issuer"),
    "Gilt": ("Debt", "Government Securities"),
    "10-year Constant Maturity Gilt": ("Debt", "Government Securities"),
    "Floating Interest Rates": ("Debt", "By Strategy"),
    "Sectoral Debt": ("Debt", "By Sector"),
    # 7 Hybrid categories.
    "Conservative Hybrid": ("Hybrid", "Hybrid Allocation"),
    "Balanced Hybrid": ("Hybrid", "Hybrid Allocation"),
    "Aggressive Hybrid": ("Hybrid", "Hybrid Allocation"),
    "Dynamic Asset Allocation": ("Hybrid", "Hybrid Allocation"),
    "Multi Asset Allocation": ("Hybrid", "Hybrid Allocation"),
    "Arbitrage": ("Hybrid", "Arbitrage Strategy"),
    "Equity Savings": ("Hybrid", "Hybrid Allocation"),
    "Life Cycle Funds": ("Life Cycle Funds", "Goal Based Glide Path"),
    "Index Funds/ETFs": ("Other Schemes", "Passive Schemes"),
    "Fund of Funds (Overseas/Domestic)": ("Other Schemes", "Fund of Funds"),
    "Retirement Fund": ("Solution Oriented (Discontinued)", "Legacy Goal Based"),
    "Children's Fund": ("Solution Oriented (Discontinued)", "Legacy Goal Based"),
    "Unclassified": ("Unclassified", "Unmapped"),
}

CATEGORY_ALIASES = {
    "multi cap fund": "Multi Cap",
    "large cap fund": "Large Cap",
    "large and mid cap fund": "Large & Mid Cap",
    "large & mid cap fund": "Large & Mid Cap",
    "mid cap fund": "Mid Cap",
    "small cap fund": "Small Cap",
    "flexi cap fund": "Flexi Cap",
    "dividend yield fund": "Dividend Yield",
    "value fund": "Value",
    "contra fund": "Contra",
    "focused fund": "Focused",
    "focused fund (no. of stocks)": "Focused",
    "sectoral fund": "Sectoral",
    "thematic fund": "Thematic",
    "elss tax saver fund": "ELSS - Tax Saver",
    "elss - tax saver fund": "ELSS - Tax Saver",
    "elss fund": "ELSS - Tax Saver",
    "overnight fund": "Overnight",
    "liquid fund": "Liquid",
    "ultra short term fund": "Ultra Short Term",
    "ultra short duration fund": "Ultra Short Term",
    "ultra short to short term fund": "Ultra Short to Short Term",
    "low duration fund": "Short Term",
    "money market fund": "Money Market",
    "short term fund": "Short Term",
    "short duration fund": "Short Term",
    "medium term fund": "Medium Term",
    "medium duration fund": "Medium Term",
    "medium to long term fund": "Medium to Long Term",
    "medium to long duration fund": "Medium to Long Term",
    "long term fund": "Long Term",
    "long duration fund": "Long Term",
    "dynamic term fund": "Dynamic Term",
    "corporate bond fund": "Corporate Bond",
    "credit risk fund": "Credit Risk",
    "banking and psu debt fund": "Banking and PSU Debt",
    "banking and psu fund": "Banking and PSU Debt",
    "gilt fund": "Gilt",
    "10 year constant maturity gilt fund": "10-year Constant Maturity Gilt",
    "10-year constant maturity gilt fund": "10-year Constant Maturity Gilt",
    "floating interest rates fund": "Floating Interest Rates",
    "floating rate fund": "Floating Interest Rates",
    "sectoral debt fund": "Sectoral Debt",
    "conservative hybrid fund": "Conservative Hybrid",
    "balanced hybrid fund": "Balanced Hybrid",
    "aggressive hybrid fund": "Aggressive Hybrid",
    "dynamic asset allocation fund": "Dynamic Asset Allocation",
    "balanced advantage fund": "Dynamic Asset Allocation",
    "multi asset allocation fund": "Multi Asset Allocation",
    "arbitrage fund": "Arbitrage",
    "sectoral/thematic fund": "Sectoral",
    "10-year constant maturity gilt": "10-year Constant Maturity Gilt",
    "10 year constant maturity gilt": "10-year Constant Maturity Gilt",
    "money market": "Money Market",
    "multi asset allocation": "Multi Asset Allocation",
    "fofs overseas/domestic": "Fund of Funds (Overseas/Domestic)",
    "equity savings fund": "Equity Savings",
    "life cycle fund": "Life Cycle Funds",
    "life cycle funds": "Life Cycle Funds",
    "index funds/etfs": "Index Funds/ETFs",
    "index fund": "Index Funds/ETFs",
    "fofs (overseas/domestic)": "Fund of Funds (Overseas/Domestic)",
    "fund of funds": "Fund of Funds (Overseas/Domestic)",
    "funds of funds": "Fund of Funds (Overseas/Domestic)",
    "retirement fund": "Retirement Fund",
    "children's fund": "Children's Fund",
    "childrens fund": "Children's Fund",
}

METRIC_COLUMNS = [
    "CAGR_1Y_pct",
    "CAGR_3Y_pct",
    "CAGR_5Y_pct",
    "Rolling_3Y_CAGR_Mean_pct",
    "Annualized_Volatility_pct",
    "Sharpe_Ratio",
    "Max_Drawdown_pct",
]

CATEGORY_SUMMARY_METRICS = [
    "CAGR_1Y_pct",
    "CAGR_3Y_pct",
    "CAGR_5Y_pct",
    "Rolling_3Y_CAGR_Mean_pct",
    "Annualized_Volatility_pct",
    "Sharpe_Ratio",
    "Max_Drawdown_pct",
    "Sortino_Ratio",
]


class MFAPIRateLimiter:
    """Thread-safe minimum spacing between outbound MFapi.in requests."""

    def __init__(self, requests_per_second: float = DEFAULT_REQUESTS_PER_SECOND):
        self.interval = 1.0 / max(float(requests_per_second), 0.05)
        self._lock = threading.Lock()
        self._next_request_at = 0.0

    def wait(self):
        with self._lock:
            now = time.monotonic()
            delay = max(0.0, self._next_request_at - now)
            if delay:
                time.sleep(delay)
            self._next_request_at = max(time.monotonic(), self._next_request_at) + self.interval


def analytics_data_dir() -> Path:
    path = Path(settings.BASE_DIR) / "data_cache" / "sebi_mf_analytics"
    path.mkdir(parents=True, exist_ok=True)
    return path


def analytics_export_dir() -> Path:
    path = Path(settings.BASE_DIR) / "exports"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _normalise_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").replace("\ufeff", "")).strip()


def _normalise_key(value: Any) -> str:
    return _normalise_text(value).casefold().replace("_", " ")


def _date_value(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    raw = _normalise_text(value)
    if not raw:
        return None
    for fmt in ("%d-%m-%Y", "%d-%b-%Y", "%d-%B-%Y", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(raw[:10]).date()
    except ValueError:
        return None


def _decimal_value(value: Any) -> float | None:
    if value in (None, "", "-"):
        return None
    try:
        cleaned = str(value).replace(",", "").replace("₹", "").replace("%", "").strip()
        number = float(cleaned)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def _extract_plan_option(name: str, plan: str = "", option: str = "") -> tuple[str, str]:
    """Infer plan/option from scheme name only when the AMFI columns are absent."""
    name_key = _normalise_key(name)
    plan_value = _normalise_text(plan)
    option_value = _normalise_text(option)
    if not plan_value:
        if re.search(r"\bdirect(?:\s+plan)?\b", name_key):
            plan_value = "Direct Plan"
        elif re.search(r"\bregular(?:\s+plan)?\b", name_key):
            plan_value = "Regular Plan"
    if not option_value:
        if re.search(r"\bidcw\b|\bdividend\b|\bpayout\b", name_key):
            option_value = "IDCW"
        elif re.search(r"\bgrowth\b", name_key):
            option_value = "Growth"
    if "growth" in _normalise_key(option_value):
        option_value = "Growth"
    elif "idcw" in _normalise_key(option_value) or "dividend" in _normalise_key(option_value):
        option_value = "IDCW"
    if "direct" in _normalise_key(plan_value):
        plan_value = "Direct Plan"
    elif "regular" in _normalise_key(plan_value):
        plan_value = "Regular Plan"
    return plan_value, option_value


def _category_from_raw_header(raw_header: str) -> str:
    """Remove AMFI's scheme-type wrapper while preserving nested FoF labels."""
    category = _normalise_text(raw_header)
    if not category:
        return ""

    category = re.sub(
        r"^(?:Open|Close|Interval)\s+Ended\s+Schemes\s*[-:]?\s*",
        "",
        category,
        flags=re.IGNORECASE,
    ).strip()

    # AMFI wraps category names in one outer parenthesis pair. Strip only
    # that pair rather than using a non-greedy regex, which truncates nested
    # labels such as "FoFs (Overseas/Domestic)".
    if category.startswith("(") and category.endswith(")"):
        category = category[1:-1].strip()

    category = re.sub(
        r"^(?:Equity|Debt|Hybrid|Solution\s+Oriented|Other)\s+Schemes?\s*[-:]\s*",
        "",
        category,
        flags=re.IGNORECASE,
    )
    category = re.sub(
        r"^(?:Equity|Debt|Hybrid|Solution\s+Oriented|Other)\s+Scheme\s*[-:]\s*",
        "",
        category,
        flags=re.IGNORECASE,
    )
    return _normalise_text(category).strip(" -:")


def map_sebi_hierarchy(raw_header: str, scheme_name: str = "") -> dict[str, str]:
    """Map the raw AMFI scheme-category header to the current SEBI 3-level tree."""
    category_raw = _category_from_raw_header(raw_header)
    key = _normalise_key(category_raw)
    category = CATEGORY_ALIASES.get(key)

    # AMFI sometimes publishes one combined "Sectoral/Thematic" raw header.
    # The 2026 SEBI taxonomy separates the two; use the scheme's own category
    # label where it is explicit instead of classifying every row as Sectoral.
    if key in {"sectoral/thematic", "sectoral/thematic fund", "sectoral or thematic fund"}:
        name_key = _normalise_key(scheme_name)
        category = "Thematic" if "thematic" in name_key else "Sectoral"

    # SEBI uses the leaf label "Sectoral Fund" in both Equity and Debt.
    # Preserve the distinction using the branch in the original AMFI header.
    if key == "sectoral fund" and re.search(r"\bDebt\s+Scheme\b", raw_header, re.IGNORECASE):
        category = "Sectoral Debt"

    if category is None:
        # A small name fallback handles six-column legacy feeds whose category
        # header is missing. Unknown names remain explicitly unclassified.
        name_key = _normalise_key(scheme_name)
        for needle, candidate in (
            ("life cycle", "Life Cycle Funds"),
            ("large & mid cap", "Large & Mid Cap"),
            ("large and mid cap", "Large & Mid Cap"),
            ("multi cap", "Multi Cap"),
            ("large cap", "Large Cap"),
            ("mid cap", "Mid Cap"),
            ("small cap", "Small Cap"),
            ("flexi cap", "Flexi Cap"),
            ("balanced advantage", "Dynamic Asset Allocation"),
            ("dynamic asset allocation", "Dynamic Asset Allocation"),
            ("aggressive hybrid", "Aggressive Hybrid"),
            ("conservative hybrid", "Conservative Hybrid"),
            ("balanced hybrid", "Balanced Hybrid"),
            ("corporate bond", "Corporate Bond"),
            ("credit risk", "Credit Risk"),
            ("liquid", "Liquid"),
            ("overnight", "Overnight"),
            ("index fund", "Index Funds/ETFs"),
            ("elss", "ELSS - Tax Saver"),
            ("retirement fund", "Retirement Fund"),
            ("children's fund", "Children's Fund"),
        ):
            if needle in name_key:
                category = candidate
                break

    if category is None:
        category = "Unclassified"

    level_1, level_2 = CATEGORY_TREE.get(category, CATEGORY_TREE["Unclassified"])
    discontinued = level_1 == "Solution Oriented (Discontinued)"
    status = "DISCONTINUED" if discontinued else (
        "UNMAPPED" if category == "Unclassified" else "MAPPED"
    )
    return {
        "level_1_branch": level_1,
        "level_2_group": level_2,
        "level_3_category": category,
        "classification_status": status,
        "category_label_from_header": category_raw,
    }


def parse_amfi_navall_feed(source_text: str) -> list[dict[str, Any]]:
    """Parse AMFI NAVAll.txt (both six- and eight-column layouts).

    AMC and raw category headers are interleaved with scheme rows; both are
    retained rather than guessed later from a scheme name.
    """
    amc_name = ""
    raw_category_header = ""
    records: dict[str, dict[str, Any]] = {}
    category_header_re = re.compile(
        r"^(?:Open|Close|Interval)\s+Ended\s+Schemes",
        re.IGNORECASE,
    )

    for raw_line in source_text.splitlines():
        original = raw_line.replace("\ufeff", "").strip()
        if not original:
            continue
        parts = [_normalise_text(part) for part in original.split(";")]
        first = parts[0] if parts else ""

        if not first.isdigit():
            if _normalise_key(first) in {"scheme code", "schemecode"}:
                continue
            if category_header_re.match(first):
                raw_category_header = first
                amc_name = ""
            elif first:
                amc_name = first
            continue

        if len(parts) < 6:
            continue

        scheme_code = first
        isin_growth = parts[1] if len(parts) > 1 and parts[1] not in {"", "-"} else ""
        isin_dividend = parts[2] if len(parts) > 2 and parts[2] not in {"", "-"} else ""
        scheme_name = parts[3] if len(parts) > 3 else ""
        if not scheme_name:
            continue

        plan = ""
        option = ""
        nav = None
        nav_date = None
        if len(parts) >= 8:
            plan = parts[4]
            option = parts[5]
            nav = _decimal_value(parts[6])
            nav_date = _date_value(parts[7])
        else:
            nav = _decimal_value(parts[4])
            nav_date = _date_value(parts[5])

        if nav is None or nav <= 0 or nav_date is None:
            continue

        plan, option = _extract_plan_option(scheme_name, plan, option)
        hierarchy = map_sebi_hierarchy(raw_category_header, scheme_name)
        row = {
            "scheme_code": scheme_code,
            "scheme_name": scheme_name,
            "isin_growth": isin_growth,
            "isin_dividend": isin_dividend,
            "isin": isin_growth or isin_dividend,
            "amc_name": amc_name,
            "raw_category_header": raw_category_header,
            "plan": plan,
            "option": option,
            "nav": nav,
            "date": nav_date,
            "is_growth_plan": option == "Growth" and plan in {"Direct Plan", "Regular Plan"},
            "is_direct_plan_growth": plan == "Direct Plan" and option == "Growth",
            **hierarchy,
        }
        previous = records.get(scheme_code)
        if previous is None or row["date"] >= previous["date"]:
            records[scheme_code] = row

    return sorted(records.values(), key=lambda row: (row["level_1_branch"], row["level_3_category"], row["scheme_name"]))


def download_amfi_navall(timeout: int = 60) -> str:
    response = requests.get(
        AMFI_NAVALL_URL,
        headers={
            "User-Agent": "PWMS-SEBI-MF-Analytics/1.0",
            "Accept": "text/plain,text/*;q=0.9,*/*;q=0.8",
            "Referer": "https://www.amfiindia.com/net-asset-value/nav-download",
        },
        timeout=timeout,
    )
    response.raise_for_status()
    if not response.text or "scheme code" not in response.text.lower():
        raise RuntimeError("AMFI NAVAll response did not contain the expected scheme-code header.")
    return response.text


def persist_sebi_universe(records: list[dict[str, Any]]) -> dict[str, int]:
    """Bulk-upsert all observed scheme variants without changing family holdings."""
    if not records:
        raise ValueError("No valid AMFI scheme rows were parsed; existing universe was left unchanged.")

    seen_at = timezone.now()
    scheme_objects = []
    latest_nav_rows = []

    # Membership changes are committed atomically with the valid non-empty feed.
    # Do not change AMFIMasterScheme.is_active (used by other PWMS flows).
    for row in records:
        scheme_objects.append(
            AMFIMasterScheme(
                scheme_code=row["scheme_code"],
                scheme_name=row["scheme_name"],
                isin_growth=row.get("isin_growth") or None,
                isin_dividend=row.get("isin_dividend") or None,
                amc_name=row.get("amc_name") or "",
                raw_category_header=row.get("raw_category_header") or "",
                plan=row.get("plan") or "",
                option=row.get("option") or "",
                level_1_branch=row.get("level_1_branch") or "",
                level_2_group=row.get("level_2_group") or "",
                level_3_category=row.get("level_3_category") or "",
                is_growth_plan=bool(row.get("is_growth_plan")),
                is_direct_plan_growth=bool(row.get("is_direct_plan_growth")),
                sebi_universe_active=bool(row.get("is_growth_plan")),
                amfi_source_date=row.get("date"),
                universe_last_seen_at=seen_at,
            )
        )

    with transaction.atomic():
        AMFIMasterScheme.objects.filter(sebi_universe_active=True).update(sebi_universe_active=False)
        AMFIMasterScheme.objects.bulk_create(
            scheme_objects,
            batch_size=1000,
            update_conflicts=True,
            unique_fields=["scheme_code"],
            update_fields=[
                "scheme_name",
                "isin_growth",
                "isin_dividend",
                "amc_name",
                "raw_category_header",
                "plan",
                "option",
                "level_1_branch",
                "level_2_group",
                "level_3_category",
                "is_growth_plan",
                "is_direct_plan_growth",
                "sebi_universe_active",
                "amfi_source_date",
                "universe_last_seen_at",
                "updated_at",
            ],
        )

        scheme_ids = dict(
            AMFIMasterScheme.objects.filter(
                scheme_code__in=[row["scheme_code"] for row in records]
            ).values_list("scheme_code", "id")
        )
        for row in records:
            scheme_id = scheme_ids.get(row["scheme_code"])
            if scheme_id is None:
                continue
            latest_nav_rows.append(
                AMFIMasterNAV(
                    scheme_id=scheme_id,
                    date=row["date"],
                    nav=row["nav"],
                    source="AMFI_NAVALL",
                )
            )
        if latest_nav_rows:
            AMFIMasterNAV.objects.bulk_create(
                latest_nav_rows,
                batch_size=2000,
                update_conflicts=True,
                unique_fields=["scheme", "date", "source"],
                update_fields=["nav"],
            )

    return {
        "schemes_seen": len(records),
        "growth_schemes": sum(1 for row in records if row.get("is_growth_plan")),
        "direct_growth_schemes": sum(1 for row in records if row.get("is_direct_plan_growth")),
        "unmapped_schemes": sum(1 for row in records if row.get("classification_status") == "UNMAPPED"),
        "discontinued_solution_oriented": sum(1 for row in records if row.get("classification_status") == "DISCONTINUED"),
    }


def load_growth_universe(
    plans: str = "direct",
    categories: list[str] | None = None,
    max_schemes: int | None = None,
) -> list[dict[str, Any]]:
    queryset = AMFIMasterScheme.objects.filter(sebi_universe_active=True)
    if plans == "direct":
        queryset = queryset.filter(is_direct_plan_growth=True)
    elif plans == "both":
        queryset = queryset.filter(is_growth_plan=True)
    else:
        raise ValueError("plans must be 'direct' or 'both'.")

    if categories:
        queryset = queryset.filter(level_3_category__in=categories)

    rows = list(
        queryset.order_by("level_1_branch", "level_3_category", "scheme_name").values(
            "scheme_code",
            "scheme_name",
            "isin_growth",
            "isin_dividend",
            "amc_name",
            "raw_category_header",
            "plan",
            "option",
            "level_1_branch",
            "level_2_group",
            "level_3_category",
            "is_direct_plan_growth",
            "amfi_source_date",
        )
    )
    if max_schemes:
        rows = rows[:max(1, int(max_schemes))]
    return rows


def _nav_cache_path(scheme_code: str) -> Path:
    safe_code = re.sub(r"[^A-Za-z0-9_-]", "_", str(scheme_code))
    path = analytics_data_dir() / "nav" / f"{safe_code}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _write_json_atomic(path: Path, payload: dict[str, Any]):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    temporary.replace(path)


def _valid_mfapi_payload(payload: Any) -> bool:
    return (
        isinstance(payload, dict)
        and str(payload.get("status", "SUCCESS")).upper() == "SUCCESS"
        and isinstance(payload.get("data"), list)
        and bool(payload.get("data"))
    )


def fetch_mfapi_nav_history(
    scheme: dict[str, Any],
    limiter: MFAPIRateLimiter,
    refresh_cache: bool = False,
    retries: int = 3,
    timeout: int = 45,
) -> dict[str, Any]:
    """Read a valid local cache first; call MFapi.in only when cache is absent/stale."""
    scheme_code = str(scheme["scheme_code"])
    cache_path = _nav_cache_path(scheme_code)
    if cache_path.is_file() and not refresh_cache:
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            if _valid_mfapi_payload(cached):
                return {"scheme_code": scheme_code, "payload": cached, "cached": True, "error": None}
        except (OSError, json.JSONDecodeError):
            logger.warning("MFapi cache was unreadable; refetching scheme=%s", scheme_code)

    url = f"{MFAPI_BASE_URL}/{scheme_code}"
    last_error: Exception | None = None
    for attempt in range(max(1, int(retries))):
        limiter.wait()
        try:
            response = requests.get(
                url,
                headers={
                    "User-Agent": "PWMS-SEBI-MF-Analytics/1.0",
                    "Accept": "application/json",
                },
                timeout=timeout,
            )
            if response.status_code == 429:
                retry_after = _decimal_value(response.headers.get("Retry-After"))
                time.sleep(max(1.0, retry_after or (2 ** attempt)))
                raise requests.HTTPError("MFapi.in rate limit returned HTTP 429.", response=response)
            response.raise_for_status()
            payload = response.json()
            if not _valid_mfapi_payload(payload):
                raise ValueError(f"MFapi.in returned no valid NAV history for scheme {scheme_code}.")
            _write_json_atomic(
                cache_path,
                {
                    "cache_source": MFAPI_SOURCE,
                    "cached_at": timezone.now().isoformat(),
                    **payload,
                },
            )
            return {"scheme_code": scheme_code, "payload": payload, "cached": False, "error": None}
        except (requests.RequestException, ValueError, json.JSONDecodeError) as exc:
            last_error = exc
            if attempt + 1 < max(1, int(retries)):
                time.sleep(min(30.0, 1.5 * (2 ** attempt)))

    return {"scheme_code": scheme_code, "payload": None, "cached": False, "error": str(last_error or "Unknown MFapi.in error.")}


def fetch_nav_histories(
    schemes: list[dict[str, Any]],
    workers: int = DEFAULT_WORKERS,
    requests_per_second: float = DEFAULT_REQUESTS_PER_SECOND,
    refresh_cache: bool = False,
    retries: int = 3,
) -> tuple[dict[str, dict[str, Any]], dict[str, str], int]:
    limiter = MFAPIRateLimiter(requests_per_second=requests_per_second)
    payloads: dict[str, dict[str, Any]] = {}
    errors: dict[str, str] = {}
    cache_hits = 0

    worker_count = max(1, min(int(workers), 16))
    with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="mfapi-nav") as executor:
        futures = {
            executor.submit(fetch_mfapi_nav_history, scheme, limiter, refresh_cache, retries): scheme
            for scheme in schemes
        }
        for future in as_completed(futures):
            scheme = futures[future]
            code = str(scheme["scheme_code"])
            try:
                result = future.result()
            except Exception as exc:
                errors[code] = str(exc)
                continue
            if result["error"]:
                errors[code] = result["error"]
                continue
            payloads[code] = result["payload"]
            cache_hits += int(bool(result["cached"]))

    return payloads, errors, cache_hits


def _normalise_nav_series(payload: dict[str, Any]) -> list[tuple[date, float]]:
    data = payload.get("data") or []
    by_date: dict[date, float] = {}
    for row in data:
        if not isinstance(row, dict):
            continue
        nav_date = _date_value(row.get("date"))
        nav = _decimal_value(row.get("nav"))
        if nav_date and nav and nav > 0:
            by_date[nav_date] = nav
    return sorted(by_date.items(), key=lambda item: item[0])


def _anniversary(value: date, years: int) -> date:
    try:
        return value.replace(year=value.year - years)
    except ValueError:
        return value.replace(year=value.year - years, day=28)


def _cagr(start_nav: float | None, end_nav: float | None, start_date: date | None, end_date: date | None) -> float | None:
    if not start_nav or not end_nav or start_nav <= 0 or end_nav <= 0 or not start_date or not end_date:
        return None
    elapsed_years = (end_date - start_date).days / 365.25
    if elapsed_years <= 0:
        return None
    try:
        return (pow(end_nav / start_nav, 1.0 / elapsed_years) - 1.0) * 100.0
    except (ValueError, OverflowError, ZeroDivisionError):
        return None


def compute_fund_performance_metrics(
    series: list[tuple[date, float]],
    risk_free_rate: float = DEFAULT_RISK_FREE_RATE,
) -> dict[str, Any]:
    """Compute trailing CAGR, daily rolling returns, risk and drawdown statistics."""
    if not series:
        return {}
    dates = [item[0] for item in series]
    navs = [float(item[1]) for item in series]
    latest_date, latest_nav = dates[-1], navs[-1]
    metrics: dict[str, Any] = {
        "NAV_Observations": len(series),
        "NAV_Start_Date": dates[0].isoformat(),
        "NAV_End_Date": latest_date.isoformat(),
        "NAV_Latest": latest_nav,
    }

    for years in (1, 3, 5, 10):
        target_date = _anniversary(latest_date, years)
        index = bisect_right(dates, target_date) - 1
        if index >= 0:
            metrics[f"CAGR_{years}Y_pct"] = _cagr(
                navs[index], latest_nav, dates[index], latest_date
            )
        else:
            metrics[f"CAGR_{years}Y_pct"] = None

    rolling_cagrs: list[float] = []
    for end_index, end_date in enumerate(dates):
        target_date = _anniversary(end_date, 3)
        start_index = bisect_right(dates, target_date, 0, end_index + 1) - 1
        if start_index < 0 or start_index >= end_index:
            continue
        result = _cagr(
            navs[start_index],
            navs[end_index],
            dates[start_index],
            end_date,
        )
        if result is not None and math.isfinite(result):
            rolling_cagrs.append(result)

    metrics["Rolling_3Y_Observations"] = len(rolling_cagrs)
    metrics["Rolling_3Y_CAGR_Mean_pct"] = (
        sum(rolling_cagrs) / len(rolling_cagrs) if rolling_cagrs else None
    )
    metrics["Rolling_3Y_CAGR_Min_pct"] = min(rolling_cagrs) if rolling_cagrs else None
    metrics["Rolling_3Y_CAGR_Max_pct"] = max(rolling_cagrs) if rolling_cagrs else None

    # Use the most recent three-year daily NAV window for risk-adjusted metrics.
    # When a fund is younger than three years, use all available observations and
    # expose the observed window so users do not mistake it for a full 3Y sample.
    risk_target = _anniversary(latest_date, 3)
    risk_start_index = max(0, bisect_right(dates, risk_target) - 1)
    risk_dates = dates[risk_start_index:]
    risk_navs = navs[risk_start_index:]
    daily_returns = [
        (risk_navs[index] / risk_navs[index - 1]) - 1.0
        for index in range(1, len(risk_navs))
        if risk_navs[index - 1] > 0
    ]
    metrics["Risk_Window_Start_Date"] = risk_dates[0].isoformat() if risk_dates else None
    metrics["Risk_Window_Days"] = (latest_date - risk_dates[0]).days if risk_dates else 0
    metrics["Risk_Return_Observations"] = len(daily_returns)
    if len(daily_returns) >= 2:
        mean_daily = sum(daily_returns) / len(daily_returns)
        daily_variance = sum((value - mean_daily) ** 2 for value in daily_returns) / (len(daily_returns) - 1)
        daily_std = math.sqrt(max(0.0, daily_variance))
        annualized_volatility = daily_std * math.sqrt(TRADING_DAYS_PER_YEAR) * 100.0
        daily_risk_free = pow(1.0 + risk_free_rate, 1.0 / TRADING_DAYS_PER_YEAR) - 1.0
        excess_returns = [value - daily_risk_free for value in daily_returns]
        mean_excess = sum(excess_returns) / len(excess_returns)
        downside_squares = [min(value, 0.0) ** 2 for value in excess_returns]
        downside_deviation = math.sqrt(sum(downside_squares) / len(downside_squares))
        metrics["Annualized_Volatility_pct"] = annualized_volatility
        metrics["Sharpe_Ratio"] = (
            (mean_excess / daily_std) * math.sqrt(TRADING_DAYS_PER_YEAR)
            if daily_std > 0 else None
        )
        metrics["Sortino_Ratio"] = (
            (mean_excess / downside_deviation) * math.sqrt(TRADING_DAYS_PER_YEAR)
            if downside_deviation > 0 else None
        )
    else:
        metrics["Annualized_Volatility_pct"] = None
        metrics["Sharpe_Ratio"] = None
        metrics["Sortino_Ratio"] = None

    running_peak = navs[0]
    running_peak_index = 0
    worst_drawdown = 0.0
    worst_peak_index = 0
    worst_trough_index = 0
    for index, nav in enumerate(navs):
        if nav >= running_peak:
            running_peak = nav
            running_peak_index = index
        drawdown = (nav / running_peak) - 1.0 if running_peak > 0 else 0.0
        if drawdown < worst_drawdown:
            worst_drawdown = drawdown
            worst_peak_index = running_peak_index
            worst_trough_index = index

    metrics["Max_Drawdown_pct"] = worst_drawdown * 100.0
    metrics["Max_Drawdown_Peak_to_Trough_Days"] = (
        (dates[worst_trough_index] - dates[worst_peak_index]).days
        if worst_drawdown < 0 else 0
    )
    if worst_drawdown < 0:
        peak_nav = navs[worst_peak_index]
        recovery_index = next(
            (index for index in range(worst_trough_index + 1, len(navs)) if navs[index] >= peak_nav),
            None,
        )
        metrics["Max_Drawdown_Recovery_Days"] = (
            (dates[recovery_index] - dates[worst_trough_index]).days
            if recovery_index is not None else None
        )
        metrics["Max_Drawdown_Unrecovered_Days"] = (
            None if recovery_index is not None else (dates[-1] - dates[worst_trough_index]).days
        )
    else:
        metrics["Max_Drawdown_Recovery_Days"] = 0
        metrics["Max_Drawdown_Unrecovered_Days"] = 0

    return metrics


def _safe_records(frame: pd.DataFrame) -> list[dict[str, Any]]:
    return json.loads(frame.to_json(orient="records", date_format="iso")) if not frame.empty else []


def _read_optional_table(path: str | Path) -> pd.DataFrame:
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"Optional data file does not exist: {path}")
    if path.suffix.lower() == ".csv":
        return pd.read_csv(path)
    if path.suffix.lower() in {".xlsx", ".xlsm"}:
        return pd.read_excel(path)
    raise ValueError(f"Unsupported optional data format: {path.suffix}. Use .xlsx or .csv.")


def _column_lookup(frame: pd.DataFrame) -> dict[str, str]:
    return {_normalise_key(column): column for column in frame.columns}


def _find_column(frame: pd.DataFrame, aliases: tuple[str, ...]) -> str | None:
    columns = _column_lookup(frame)
    for alias in aliases:
        if _normalise_key(alias) in columns:
            return columns[_normalise_key(alias)]
    for key, original in columns.items():
        if any(_normalise_key(alias) in key for alias in aliases):
            return original
    return None


def load_expense_ratio_data(path: str | Path) -> dict[str, float]:
    """Read optional AMC TER spreadsheets with a scheme-code or ISIN column."""
    frame = _read_optional_table(path)
    identity_col = _find_column(frame, ("scheme code", "scheme_code", "amfi code", "isin"))
    expense_col = _find_column(frame, ("expense ratio", "ter", "total expense ratio", "expense_ratio_pct"))
    if not identity_col or not expense_col:
        raise ValueError("TER file must contain Scheme Code or ISIN plus Expense Ratio / TER columns.")
    result = {}
    for _, row in frame.iterrows():
        identity = _normalise_text(row.get(identity_col)).upper()
        expense = _decimal_value(row.get(expense_col))
        if identity and expense is not None:
            result[identity] = expense
    return result


def load_category_flow_data(path: str | Path) -> dict[str, dict[str, float]]:
    """Read optional AMFI monthly category AUM/net-flow tables exported to XLSX/CSV."""
    frame = _read_optional_table(path)
    category_col = _find_column(frame, ("level 3 category", "category", "scheme category"))
    aum_col = _find_column(frame, ("aum cr", "aum in cr", "average aum", "aum"))
    flow_col = _find_column(frame, ("net flows cr", "net flow", "net inflow", "net flows"))
    if not category_col:
        raise ValueError("Category flow file must contain a Category / Level 3 Category column.")
    result = {}
    for _, row in frame.iterrows():
        category = _normalise_text(row.get(category_col))
        if not category:
            continue
        result[_normalise_key(category)] = {
            "Category_AUM_Cr": _decimal_value(row.get(aum_col)) if aum_col else None,
            "Category_Net_Flows_Cr": _decimal_value(row.get(flow_col)) if flow_col else None,
        }
    return result


def _trailing_years(value: Any) -> int | None:
    try:
        return int(str(value)[:4])
    except (TypeError, ValueError):
        return None


def _add_category_benchmarks(
    funds: pd.DataFrame,
    plans: list[str],
    category_flow_data: dict[str, dict[str, float]] | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    observed_categories = sorted(
        set(funds["Level_3_Category"].dropna().astype(str))
    )
    taxonomy_categories = sorted(
        category for category, (branch, _group) in CATEGORY_TREE.items()
        if category != "Unclassified" and branch != "Solution Oriented (Discontinued)"
    )
    all_categories = sorted(set(observed_categories) | set(taxonomy_categories))

    rows = []
    for category in all_categories:
        level_1, level_2 = CATEGORY_TREE.get(category, CATEGORY_TREE["Unclassified"])
        for plan in plans:
            subset = funds[
                (funds["Level_3_Category"] == category)
                & (funds["Plan"] == plan)
            ]
            row: dict[str, Any] = {
                "Level_1_Branch": level_1,
                "Level_2_Group": level_2,
                "Level_3_Category": category,
                "Plan": plan,
                "Fund_Count": int(len(subset)),
            }
            for metric in CATEGORY_SUMMARY_METRICS:
                row[f"Category_Avg_{metric}"] = (
                    float(subset[metric].mean()) if metric in subset and subset[metric].notna().any() else None
                )
                row[f"Category_Median_{metric}"] = (
                    float(subset[metric].median()) if metric in subset and subset[metric].notna().any() else None
                )
            flow = (category_flow_data or {}).get(_normalise_key(category), {})
            row.update(flow)
            rows.append(row)

    summary = pd.DataFrame(rows)
    ranked = funds.copy()
    if summary.empty:
        return summary, ranked

    avg_columns = [
        column for column in summary.columns
        if column.startswith("Category_Avg_") or column.startswith("Category_Median_")
    ]
    ranked = ranked.merge(
        summary[["Level_3_Category", "Plan", "Fund_Count"] + avg_columns],
        on=["Level_3_Category", "Plan"],
        how="left",
        suffixes=("", "_Peer"),
    )

    ranked["Outperformance_bps"] = (
        ranked["CAGR_3Y_pct"] - ranked["Category_Avg_CAGR_3Y_pct"]
    ) * 100.0
    ranked["Outperformance_1Y_bps"] = (
        ranked["CAGR_1Y_pct"] - ranked["Category_Avg_CAGR_1Y_pct"]
    ) * 100.0
    ranked["Outperformance_5Y_bps"] = (
        ranked["CAGR_5Y_pct"] - ranked["Category_Avg_CAGR_5Y_pct"]
    ) * 100.0
    ranked["Ranking_Period"] = "3Y CAGR"

    ranked["Percentile_Rank_3Y"] = None
    ranked["Quartile"] = "N/A"
    ranked["Beat_Category_Avg"] = None
    for _, indices in ranked.groupby(["Level_3_Category", "Plan"], sort=False).groups.items():
        group = ranked.loc[indices]
        valid = group["CAGR_3Y_pct"].dropna()
        if valid.empty:
            continue
        percentile = valid.rank(method="average", ascending=True, pct=True) * 100.0
        ranked.loc[percentile.index, "Percentile_Rank_3Y"] = percentile
        for index, value in percentile.items():
            ranked.at[index, "Quartile"] = (
                "Q1" if value >= 75 else
                "Q2" if value >= 50 else
                "Q3" if value >= 25 else "Q4"
            )
        ranked.loc[valid.index, "Beat_Category_Avg"] = (
            valid > ranked.loc[valid.index, "Category_Avg_CAGR_3Y_pct"]
        )

    if "Expense_Ratio_pct" not in ranked:
        ranked["Expense_Ratio_pct"] = None
    ranked = ranked.sort_values(
        ["Level_1_Branch", "Level_2_Group", "Level_3_Category", "Plan", "Percentile_Rank_3Y"],
        ascending=[True, True, True, True, False],
        na_position="last",
    )
    return summary, ranked


def _enrich_expense_ratios(funds: pd.DataFrame, expense_data: dict[str, float] | None) -> pd.DataFrame:
    result = funds.copy()
    if not expense_data:
        result["Expense_Ratio_pct"] = None
        return result
    values = []
    for _, row in result.iterrows():
        code = _normalise_text(row.get("Scheme_Code")).upper()
        isin = _normalise_text(row.get("ISIN")).upper()
        values.append(expense_data.get(code, expense_data.get(isin)))
    result["Expense_Ratio_pct"] = values
    return result


def _excel_overlap_sheet(portfolios: list[dict[str, Any]]) -> pd.DataFrame:
    rows = calculate_pairwise_portfolio_overlap(portfolios)
    return pd.DataFrame(rows)


def calculate_pairwise_portfolio_overlap(portfolios: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Calculate SEBI overlap as sum(min(weight A, weight B)) by common ISIN."""
    results = []
    eligible = [
        portfolio for portfolio in portfolios
        if portfolio.get("level_3_category") and portfolio.get("holdings")
    ]
    for left, right in combinations(eligible, 2):
        if left.get("level_3_category") != right.get("level_3_category"):
            continue
        left_weights = {
            str(row.get("isin") or "").strip().upper(): float(row.get("weight_pct") or 0)
            for row in left.get("holdings", [])
            if str(row.get("isin") or "").strip()
        }
        right_weights = {
            str(row.get("isin") or "").strip().upper(): float(row.get("weight_pct") or 0)
            for row in right.get("holdings", [])
            if str(row.get("isin") or "").strip()
        }
        common_isins = set(left_weights) & set(right_weights)
        overlap = sum(min(left_weights[isin], right_weights[isin]) for isin in common_isins)
        results.append({
            "Level_3_Category": left.get("level_3_category"),
            "Scheme_A_Code": left.get("scheme_code"),
            "Scheme_A_Name": left.get("scheme_name"),
            "Plan_A": left.get("plan"),
            "Portfolio_A_Date": left.get("portfolio_date"),
            "Scheme_B_Code": right.get("scheme_code"),
            "Scheme_B_Name": right.get("scheme_name"),
            "Plan_B": right.get("plan"),
            "Portfolio_B_Date": right.get("portfolio_date"),
            "Common_ISIN_Count": len(common_isins),
            "Portfolio_Overlap_pct": round(overlap, 4),
        })
    return sorted(results, key=lambda row: (row["Level_3_Category"], -row["Portfolio_Overlap_pct"]))


def import_monthly_portfolio_holdings_xlsx(
    file_path: str | Path,
    scheme_code: str,
    portfolio_date: date | None = None,
) -> dict[str, Any]:
    """Import one AMC monthly portfolio xlsx, keyed by ISIN for overlap analysis."""
    file_path = Path(file_path)
    if not file_path.is_file():
        raise FileNotFoundError(f"Portfolio workbook does not exist: {file_path}")
    if file_path.suffix.lower() != ".xlsx":
        raise ValueError("Monthly portfolio import currently accepts .xlsx files.")

    scheme = AMFIMasterScheme.objects.filter(scheme_code=str(scheme_code)).first()
    if scheme is None:
        raise ValueError(f"AMFI scheme code {scheme_code} is not in the imported SEBI universe.")
    if not scheme.level_3_category:
        raise ValueError(f"Scheme {scheme_code} has no mapped SEBI Level 3 category.")

    workbook = load_workbook(file_path, read_only=True, data_only=True)
    try:
        worksheet = workbook.active
        rows = worksheet.iter_rows()
        header_cells = next(rows, None)
        if not header_cells:
            raise ValueError("Portfolio workbook is empty.")
        headers = [_normalise_key(cell.value) for cell in header_cells]
        positions = {header: index for index, header in enumerate(headers) if header}

        def column_index(aliases: tuple[str, ...]) -> int | None:
            for alias in aliases:
                key = _normalise_key(alias)
                if key in positions:
                    return positions[key]
            for header, index in positions.items():
                if any(_normalise_key(alias) in header for alias in aliases):
                    return index
            return None

        isin_index = column_index(("isin", "security isin"))
        stock_index = column_index(("stock name", "security name", "instrument name", "name"))
        sector_index = column_index(("sector", "industry"))
        weight_index = column_index(("% to net assets", "percent to net assets", "percentage to net assets", "net assets %", "holding %", "weight %"))
        if isin_index is None or weight_index is None:
            raise ValueError("Portfolio workbook must contain ISIN and % to Net Assets columns.")

        holdings_by_isin: dict[str, dict[str, Any]] = {}
        for row in rows:
            if isin_index >= len(row) or weight_index >= len(row):
                continue
            isin = _normalise_text(row[isin_index].value).upper()
            if not isin or isin in {"-", "N/A", "NA"}:
                continue
            weight_cell = row[weight_index]
            raw_weight = weight_cell.value
            weight = _decimal_value(raw_weight)
            if isinstance(raw_weight, str) and "%" in raw_weight:
                weight = _decimal_value(raw_weight)
            elif weight is not None and "%" in str(getattr(weight_cell, "number_format", "")):
                weight *= 100.0
            if weight is None or weight < 0:
                continue
            stock_name = _normalise_text(row[stock_index].value) if stock_index is not None and stock_index < len(row) else ""
            sector = _normalise_text(row[sector_index].value) if sector_index is not None and sector_index < len(row) else ""
            existing = holdings_by_isin.get(isin)
            if existing:
                existing["weight_pct"] += weight
                if not existing.get("stock_name") and stock_name:
                    existing["stock_name"] = stock_name
                if not existing.get("sector") and sector:
                    existing["sector"] = sector
            else:
                holdings_by_isin[isin] = {
                    "isin": isin,
                    "stock_name": stock_name,
                    "sector": sector,
                    "weight_pct": weight,
                }
    finally:
        workbook.close()

    if not holdings_by_isin:
        raise ValueError("No valid ISIN-weight rows were found in the portfolio workbook.")

    if portfolio_date is None:
        raise ValueError(
            "Provide the portfolio disclosure date explicitly with --portfolio-date; "
            "a file modification timestamp is not a reliable disclosure date."
        )
    as_of = portfolio_date
    payload = {
        "scheme_code": scheme.scheme_code,
        "scheme_name": scheme.scheme_name,
        "isin": scheme.isin_growth or scheme.isin_dividend,
        "amc_name": scheme.amc_name,
        "level_1_branch": scheme.level_1_branch,
        "level_2_group": scheme.level_2_group,
        "level_3_category": scheme.level_3_category,
        "plan": scheme.plan,
        "portfolio_date": as_of.isoformat(),
        "source_file": file_path.name,
        "holdings": sorted(holdings_by_isin.values(), key=lambda row: row["weight_pct"], reverse=True),
    }
    cache_dir = analytics_data_dir() / "portfolio_holdings"
    cache_dir.mkdir(parents=True, exist_ok=True)
    _write_json_atomic(cache_dir / f"{scheme.scheme_code}_{as_of.isoformat()}.json", payload)
    return {
        "scheme_code": scheme.scheme_code,
        "scheme_name": scheme.scheme_name,
        "portfolio_date": as_of.isoformat(),
        "holdings": len(holdings_by_isin),
        "total_weight_pct": round(sum(row["weight_pct"] for row in holdings_by_isin.values()), 4),
        "cache_directory": str(cache_dir),
    }


def load_cached_portfolio_holdings() -> list[dict[str, Any]]:
    cache_dir = analytics_data_dir() / "portfolio_holdings"
    if not cache_dir.exists():
        return []
    portfolios = []
    for path in sorted(cache_dir.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if payload.get("holdings"):
                portfolios.append(payload)
        except (OSError, json.JSONDecodeError):
            logger.warning("Skipping unreadable portfolio cache %s", path)
    # Use the latest uploaded disclosure per scheme.
    latest_by_scheme = {}
    for payload in portfolios:
        code = str(payload.get("scheme_code") or "")
        previous = latest_by_scheme.get(code)
        if previous is None or str(payload.get("portfolio_date")) > str(previous.get("portfolio_date")):
            latest_by_scheme[code] = payload
    return list(latest_by_scheme.values())


def plot_category_return_vs_risk(summary: pd.DataFrame, output_path: str | Path) -> str:
    """Plot category 3Y average CAGR against annualized NAV volatility."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    frame = summary.dropna(subset=["Category_Avg_CAGR_3Y_pct", "Category_Avg_Annualized_Volatility_pct"]).copy()
    frame = frame[frame["Fund_Count"] > 0]
    figure, axis = plt.subplots(figsize=(13, 8))
    if not frame.empty:
        axis.scatter(
            frame["Category_Avg_Annualized_Volatility_pct"],
            frame["Category_Avg_CAGR_3Y_pct"],
            alpha=0.75,
        )
        for _, row in frame.iterrows():
            axis.annotate(
                f"{row['Level_3_Category']} ({row['Plan']})",
                (row["Category_Avg_Annualized_Volatility_pct"], row["Category_Avg_CAGR_3Y_pct"]),
                fontsize=7,
                xytext=(4, 4),
                textcoords="offset points",
            )
    axis.set_title("SEBI Category Average: 3Y Return vs Annualized Risk")
    axis.set_xlabel("Annualized Volatility (%)")
    axis.set_ylabel("Category Average 3Y CAGR (%)")
    axis.grid(alpha=0.25)
    figure.tight_layout()
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(target, dpi=160)
    plt.close(figure)
    return str(target)


def plot_top_q1_funds_vs_category_average(rankings: pd.DataFrame, output_path: str | Path, limit: int = 15) -> str:
    """Compare the strongest Q1 funds to their own plan/category peer average."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    frame = rankings[
        (rankings["Quartile"] == "Q1")
        & rankings["CAGR_3Y_pct"].notna()
        & rankings["Category_Avg_CAGR_3Y_pct"].notna()
    ].sort_values("CAGR_3Y_pct", ascending=False).head(max(1, int(limit))).copy()
    figure, axis = plt.subplots(figsize=(14, 8))
    if not frame.empty:
        labels = frame["Scheme_Name"].astype(str).tolist()
        positions = list(range(len(frame)))
        width = 0.38
        axis.bar(
            [position - width / 2 for position in positions],
            frame["CAGR_3Y_pct"],
            width=width,
            label="Fund 3Y CAGR",
        )
        axis.bar(
            [position + width / 2 for position in positions],
            frame["Category_Avg_CAGR_3Y_pct"],
            width=width,
            label="Category Average 3Y CAGR",
        )
        axis.set_xticks(positions)
        axis.set_xticklabels(labels, rotation=60, ha="right", fontsize=7)
        axis.legend()
    axis.set_title("Top Q1 Funds vs Their SEBI Category Average")
    axis.set_ylabel("3Y CAGR (%)")
    axis.grid(axis="y", alpha=0.25)
    figure.tight_layout()
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(target, dpi=160)
    plt.close(figure)
    return str(target)


def export_sebi_analytics_workbook(
    category_summary: pd.DataFrame,
    rankings: pd.DataFrame,
    output_path: str | Path,
    overlap: pd.DataFrame | None = None,
) -> str:
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(target, engine="openpyxl") as writer:
        category_summary.to_excel(writer, sheet_name="Tree_Summary", index=False)
        rankings.to_excel(writer, sheet_name="Fund_vs_Category_Rankings", index=False)
        if overlap is not None and not overlap.empty:
            overlap.to_excel(writer, sheet_name="Portfolio_Overlap", index=False)

    from openpyxl import load_workbook as load_excel_workbook
    workbook = load_excel_workbook(target)
    for worksheet in workbook.worksheets:
        worksheet.freeze_panes = "A2"
        worksheet.auto_filter.ref = worksheet.dimensions
        worksheet.sheet_view.showGridLines = False
        for column_cells in worksheet.columns:
            values = [len(str(cell.value or "")) for cell in list(column_cells)[:250]]
            width = max(12, min(42, max(values, default=12) + 2))
            worksheet.column_dimensions[column_cells[0].column_letter].width = width
    workbook.save(target)
    return str(target)


def _summary_json_path() -> Path:
    return analytics_data_dir() / "latest_summary.json"


def load_latest_sebi_summary() -> dict[str, Any] | None:
    path = _summary_json_path()
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.exception("Unable to read latest SEBI analytics summary.")
        return None


def build_sebi_universe_analytics(
    plans: str = "direct",
    categories: list[str] | None = None,
    max_schemes: int | None = None,
    workers: int = DEFAULT_WORKERS,
    requests_per_second: float = DEFAULT_REQUESTS_PER_SECOND,
    refresh_cache: bool = False,
    retries: int = 3,
    risk_free_rate: float = DEFAULT_RISK_FREE_RATE,
    expense_ratio_file: str | Path | None = None,
    category_flow_file: str | Path | None = None,
    output_path: str | Path | None = None,
) -> dict[str, Any]:
    """Fetch/cache NAV history, build fund and peer metrics, then export XLSX/plots."""
    schemes = load_growth_universe(plans=plans, categories=categories, max_schemes=max_schemes)
    if not schemes:
        raise ValueError(
            "No SEBI Growth schemes are available. Run ingest_sebi_mf_universe first, "
            "then rerun this command."
        )

    payloads, errors, cache_hits = fetch_nav_histories(
        schemes,
        workers=workers,
        requests_per_second=requests_per_second,
        refresh_cache=refresh_cache,
        retries=retries,
    )

    scheme_by_code = {str(row["scheme_code"]): row for row in schemes}
    metric_rows = []
    for code, payload in payloads.items():
        series = _normalise_nav_series(payload)
        if not series:
            errors[code] = "MFapi.in response contained no valid date/NAV observations."
            continue
        scheme = scheme_by_code[code]
        meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
        metric_rows.append({
            "Scheme_Code": code,
            "ISIN": scheme.get("isin_growth") or scheme.get("isin_dividend") or meta.get("isin_growth") or "",
            "AMC_Name": scheme.get("amc_name") or meta.get("fund_house") or "",
            "Scheme_Name": scheme["scheme_name"],
            "Scheme_Category": scheme.get("raw_category_header") or meta.get("scheme_category") or "",
            "Level_1_Branch": scheme.get("level_1_branch") or "",
            "Level_2_Group": scheme.get("level_2_group") or "",
            "Level_3_Category": scheme.get("level_3_category") or "Unclassified",
            "Plan": scheme.get("plan") or "",
            "Option": scheme.get("option") or "",
            **compute_fund_performance_metrics(series, risk_free_rate=risk_free_rate),
        })

    if not metric_rows:
        raise RuntimeError(
            "No usable MFapi.in NAV histories were obtained. "
            "Existing local caches were preserved; inspect errors before retrying."
        )

    funds = pd.DataFrame(metric_rows)
    expense_data = load_expense_ratio_data(expense_ratio_file) if expense_ratio_file else None
    funds = _enrich_expense_ratios(funds, expense_data)
    category_flow_data = load_category_flow_data(category_flow_file) if category_flow_file else {}

    selected_plans = ["Direct Plan"] if plans == "direct" else ["Direct Plan", "Regular Plan"]
    category_summary, rankings = _add_category_benchmarks(
        funds,
        plans=selected_plans,
        category_flow_data=category_flow_data,
    )

    exports = analytics_export_dir()
    workbook_path = Path(output_path) if output_path else exports / "SEBI_Universe_MF_Analytics.xlsx"
    category_chart_path = exports / "SEBI_Category_Return_vs_Risk.png"
    q1_chart_path = exports / "SEBI_Top_Q1_vs_Category_Average.png"

    plot_category_return_vs_risk(category_summary, category_chart_path)
    plot_top_q1_funds_vs_category_average(rankings, q1_chart_path)

    overlap_portfolios = load_cached_portfolio_holdings()
    overlap_frame = _excel_overlap_sheet(overlap_portfolios) if overlap_portfolios else pd.DataFrame()
    export_sebi_analytics_workbook(
        category_summary,
        rankings,
        workbook_path,
        overlap=overlap_frame,
    )

    summary = {
        "available": True,
        "generated_at": timezone.now().isoformat(),
        "source": "AMFI NAVAll.txt scheme metadata + MFapi.in historical NAV cache",
        "plans_processed": selected_plans,
        "risk_free_rate_pct": round(risk_free_rate * 100.0, 4),
        "schemes_requested": len(schemes),
        "funds_with_history": len(funds),
        "funds_without_history": len(errors),
        "mfapi_cache_hits": cache_hits,
        "categories_count": int(funds["Level_3_Category"].nunique()),
        "category_tree_nodes": len(CATEGORY_TREE),
        "current_sebi_leaf_categories": sum(
            1 for category, (branch, _group) in CATEGORY_TREE.items()
            if category != "Unclassified" and branch != "Solution Oriented (Discontinued)"
        ),
        "portfolio_overlap_pairs": len(overlap_frame),
        "workbook_path": str(workbook_path),
        "category_risk_chart": str(category_chart_path),
        "top_q1_chart": str(q1_chart_path),
        "tree_summary": _safe_records(category_summary),
        "top_q1_funds": _safe_records(
            rankings[rankings["Quartile"] == "Q1"]
            .sort_values("CAGR_3Y_pct", ascending=False)
            .head(10)
        ),
        "download_url": "/api/mutual-fund-analytics/sebi/export/",
        "message": (
            "Category ranks are calculated within the exact Level 3 SEBI category and plan. "
            "Outperformance_bps is the fund's 3Y CAGR minus its peer-group average, multiplied by 100. "
            "Missing NAV history and missing disclosures remain unavailable; no values are fabricated."
        ),
        "fetch_errors": [
            {"scheme_code": code, "error": message}
            for code, message in sorted(errors.items())
        ][:500],
    }
    _write_json_atomic(_summary_json_path(), summary)
    _write_json_atomic(analytics_data_dir() / "latest_rankings.json", {"rankings": _safe_records(rankings)})
    return summary
