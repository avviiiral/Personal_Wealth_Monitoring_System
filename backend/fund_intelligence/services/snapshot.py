"""Build the Fund Intelligence snapshot for the Multi Cap Regular Growth universe.

Source order:

1. Stored watch-list data (AMFI-derived performance snapshots plus product
   master fields). This is always the primary source.
2. indianapi.in ``/mutual_funds`` as live enrichment, shown alongside the
   stored values and never overwriting them. Failures degrade to a status
   message; they never fail the page.

Values are kept to 4 decimal places here. The frontend is responsible for
displaying 2.
"""

from __future__ import annotations

import logging
import re
from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.core.cache import cache
from django.db.models import Q
from django.utils import timezone

from watchlist.models import InvestmentProduct, PerformanceSnapshot, ProductType

from fund_intelligence.services.indianapi import IndianApiClient, IndianApiError
from fund_intelligence.services.live_matching import flatten_fund_records, match_fund_records

logger = logging.getLogger(__name__)

LIVE_CACHE_KEY = "fund_intelligence:indianapi:mutual_funds:v1"
DEFAULT_LIVE_TTL_SECONDS = 6 * 60 * 60

# (response key, snapshot field)
RETURN_FIELDS = (
    ("1m", "return_1m"),
    ("3m", "return_3m"),
    ("6m", "return_6m"),
    ("1y", "return_1y"),
    ("3y", "return_3y"),
    ("5y", "return_5y"),
    ("since_inception", "return_since_inception"),
    ("cagr", "cagr"),
)
# indianapi.in field names for the same periods.
LIVE_RETURN_FIELDS = (
    ("1m", "1_month_return"),
    ("3m", "3_month_return"),
    ("6m", "6_month_return"),
    ("1y", "1_year_return"),
    ("3y", "3_year_return"),
    ("5y", "5_year_return"),
)

# What the page can and cannot show. Kept here so the UI reflects the real
# data sources rather than a hard-coded design.
DATA_COVERAGE = {
    "available": [
        "Latest NAV, AUM, expense ratio, exit load, fund manager and benchmark (watch-list master)",
        "Returns for 1M, 3M, 6M, 1Y, 3Y, 5Y, since inception and CAGR (AMFI NAV history)",
        "Live NAV, AUM, trailing returns and star rating from indianapi.in, when a key is configured",
    ],
    "not_available": [
        {"item": "Holdings and top-10 concentration", "reason": "Not stored for watch-list funds; not offered by indianapi.in mutual fund endpoints."},
        {"item": "Sector allocation and market-cap split", "reason": "Not stored for watch-list funds; not offered by indianapi.in mutual fund endpoints."},
        {"item": "Risk ratios (Sharpe, Sortino, Beta, Std dev, Drawdown, Calmar, capture ratios)", "reason": "Requires a daily NAV series and risk-free rate that the watch-list does not compute; not offered by indianapi.in."},
        {"item": "Calendar-year returns", "reason": "Not stored per year; not offered by indianapi.in mutual fund endpoints."},
        {"item": "Turnover and performance attribution", "reason": "Not stored; not offered by indianapi.in mutual fund endpoints."},
        {"item": "Category P/E and P/BV positioning", "reason": "Not stored; not offered by indianapi.in mutual fund endpoints."},
    ],
}


def _round4(value):
    if value is None:
        return None
    return float(Decimal(value).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP))


def _short_name(name: str) -> str:
    return re.split(r"\s+-\s+", name or "", maxsplit=1)[0].strip()


def _is_growth_option(option) -> bool:
    """Exclude IDCW/dividend options; keep Growth (and options AMFI left blank)."""
    if not option:
        return True
    text = option.lower()
    return "idcw" not in text and "dividend" not in text


def universe_products():
    """Active Multi Cap, Regular plan, Growth option mutual fund products."""
    queryset = (
        InvestmentProduct.objects.filter(
            product_type=ProductType.MUTUAL_FUND,
            is_active=True,
            mutual_fund__isnull=False,
        )
        .filter(Q(category__icontains="multi cap") | Q(category__icontains="multicap"))
        .filter(mutual_fund__plan__icontains="regular")
        .select_related("mutual_fund")
        .order_by("name")
    )
    return [product for product in queryset if _is_growth_option(product.mutual_fund.option)]


def latest_metric_snapshots(product_ids):
    """Latest PerformanceSnapshot per product that carries at least one metric."""
    if not product_ids:
        return {}
    metric_fields = [field for _, field in RETURN_FIELDS] + ["aum"]
    latest: dict[int, PerformanceSnapshot] = {}
    snapshots = PerformanceSnapshot.objects.filter(product_id__in=product_ids).order_by("product_id", "-date", "-id")
    for snapshot in snapshots:
        if snapshot.product_id in latest:
            continue
        if any(getattr(snapshot, field) is not None for field in metric_fields):
            latest[snapshot.product_id] = snapshot
    return latest


def _live_fields(record: dict) -> dict:
    returns = {key: _round4(_to_float(record.get(field))) for key, field in LIVE_RETURN_FIELDS}
    return {
        "fund_name": record.get("fund_name"),
        "latest_nav": _round4(_to_float(record.get("latest_nav"))),
        "aum": _round4(_to_float(record.get("asset_size"))),
        "star_rating": _to_float(record.get("star_rating")),
        "returns": returns,
    }


def _to_float(value):
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    cleaned = re.sub(r"[^0-9.\-]", "", str(value))
    if cleaned in {"", "-", ".", "-."}:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def _load_live_payload(client):
    # A cache outage must not break live enrichment: fall back to a direct fetch.
    try:
        cached = cache.get(LIVE_CACHE_KEY)
    except Exception:  # noqa: BLE001 - any cache backend failure degrades to a fetch
        logger.warning("Fund Intelligence cache read failed; fetching live data directly.", exc_info=True)
        cached = None
    if cached is not None:
        return cached
    ttl = getattr(settings, "FUND_INTELLIGENCE_LIVE_TTL_SECONDS", DEFAULT_LIVE_TTL_SECONDS)
    value = {
        "records": flatten_fund_records(client.mutual_funds()),
        "fetched_at": timezone.now().isoformat(),
    }
    try:
        cache.set(LIVE_CACHE_KEY, value, ttl)
    except Exception:  # noqa: BLE001 - the fetched value is still usable this request
        logger.warning("Fund Intelligence cache write failed; live data not cached.", exc_info=True)
    return value


def _live_state(include_live, client, names_by_id):
    state = {
        "status": "disabled",
        "message": "Live enrichment was not requested.",
        "fetched_at": None,
        "records_received": 0,
        "matched": 0,
        "unmatched": [],
    }
    matches = {}
    if not include_live:
        return state, matches

    if not client.is_configured:
        state.update(status="not_configured", message="INDIAN_API_KEY is not set; showing stored data only.")
        state["unmatched"] = list(names_by_id.values())
        return state, matches

    try:
        payload = _load_live_payload(client)
    except IndianApiError as exc:
        state.update(status="error", message=str(exc))
        state["unmatched"] = list(names_by_id.values())
        return state, matches

    matches, unmatched = match_fund_records(names_by_id, payload["records"])
    state.update(
        status="ok",
        message="Live values from indianapi.in." if payload["records"] else "indianapi.in returned no mutual fund records.",
        fetched_at=payload["fetched_at"],
        records_received=len(payload["records"]),
        matched=len(matches),
        unmatched=unmatched,
    )
    return state, matches


def _average(values):
    present = [value for value in values if value is not None]
    if not present:
        return None
    return _round4(sum(present) / len(present))


def build_fund_intelligence_snapshot(include_live=True, client=None):
    client = client or IndianApiClient()
    products = universe_products()
    snapshots = latest_metric_snapshots([product.id for product in products])
    names_by_id = {product.id: product.name for product in products}
    live, live_matches = _live_state(include_live, client, names_by_id)

    funds = []
    for product in products:
        mf = product.mutual_fund
        snapshot = snapshots.get(product.id)
        returns = {
            key: _round4(getattr(snapshot, field)) if snapshot else None
            for key, field in RETURN_FIELDS
        }
        match = live_matches.get(product.id)
        funds.append(
            {
                "product_id": product.id,
                "name": product.name,
                "short_name": _short_name(product.name),
                "provider": product.provider,
                "category": product.category,
                "isin": product.isin,
                "plan": mf.plan,
                "option": mf.option,
                "fund_manager": mf.fund_manager,
                "benchmark": mf.benchmark or product.comparison_benchmark,
                "aum": _round4(mf.aum),
                "expense_ratio": _round4(mf.expense_ratio),
                "exit_load": mf.exit_load,
                "risk_level": mf.risk_level,
                "minimum_investment": _round4(mf.minimum_investment),
                "latest_nav": _round4(mf.latest_nav),
                "latest_nav_date": mf.latest_nav_date.isoformat() if mf.latest_nav_date else None,
                "as_of": snapshot.date.isoformat() if snapshot else None,
                "returns": returns,
                "live": _live_fields(match["record"]) | {"match_score": match["score"]} if match else None,
            }
        )

    category_average = {key: _average([fund["returns"][key] for fund in funds]) for key, _ in RETURN_FIELDS}
    for fund in funds:
        fund["vs_category"] = {
            key: _round4(fund["returns"][key] - category_average[key])
            if fund["returns"][key] is not None and category_average[key] is not None
            else None
            for key, _ in RETURN_FIELDS
        }

    as_of_dates = [fund["as_of"] for fund in funds if fund["as_of"]]
    return {
        "meta": {
            "category": "Multi Cap",
            "plan": "Regular",
            "option": "Growth",
            "fund_count": len(funds),
            "stored_data_as_of": max(as_of_dates) if as_of_dates else None,
            "generated_at": timezone.now().isoformat(),
        },
        "category_average": category_average,
        "funds": funds,
        "live": live,
        "data_coverage": DATA_COVERAGE,
    }
