"""Shared parsers and persistence helpers for global mutual-fund masterdata."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from html import unescape
import io
import math
import re
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

import pandas as pd
import requests
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from mutual_funds.models import AMFIMasterScheme
from mutual_funds.master_models import MFMasterData, MFMasterHolding, MFSourceRun

FOUR_DP = Decimal("0.0001")
USER_AGENT = "PWMS-MutualFund-Masterdata/1.0 (+https://github.com/avviiiral/Personal_Wealth_Monitoring_System)"
_HOST_LOCK = threading.Lock()
_HOST_LAST_REQUEST = {}


def normalize_text(value):
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return ""
    return " ".join(unescape(str(value)).replace("\xa0", " ").strip().split())


def normalize_key(value):
    value = normalize_text(value).casefold().replace("&", " and ")
    return " ".join(re.sub(r"[^a-z0-9]+", " ", value).split())


def normalize_isin(value):
    value = re.sub(r"[^A-Za-z0-9]", "", normalize_text(value)).upper()
    return value if re.fullmatch(r"[A-Z]{2}[A-Z0-9]{9}[0-9]", value) else ""


def decimal_value(value, percent=False):
    raw = normalize_text(value)
    if not raw or raw.casefold() in {"-", "n/a", "na", "nan", "nil", "none"}:
        return None
    negative = raw.startswith("(") and raw.endswith(")")
    raw = raw.strip("()").replace(",", "").replace("₹", "").replace("Rs.", "").replace("Rs", "").replace("%", "").strip()
    try:
        number = Decimal(raw)
    except InvalidOperation:
        return None
    if negative:
        number = -number
    if not number.is_finite() or (percent and not Decimal("-100") <= number <= Decimal("100")):
        return None
    return number.quantize(FOUR_DP, rounding=ROUND_HALF_UP)


def parse_date(value):
    if value is None or normalize_text(value) == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    normalized = normalize_text(value)
    # ISO dates are unambiguous; parse them directly instead of sending them
    # through pandas with dayfirst=True (which emits a warning).
    if re.fullmatch(r"\\d{4}-\\d{2}-\\d{2}", normalized):
        try:
            return date.fromisoformat(normalized)
        except ValueError:
            return None
    parsed = pd.to_datetime(value, dayfirst=True, errors="coerce")
    return None if pd.isna(parsed) else parsed.date()


def find_column(frame, aliases):
    columns = {normalize_key(c): str(c) for c in frame.columns}
    for alias in aliases:
        key = normalize_key(alias)
        if key in columns:
            return columns[key]
    for alias in aliases:
        key = normalize_key(alias)
        for candidate, original in columns.items():
            if key and key in candidate:
                return original
    return None


def read_table(payload, filename):
    suffix = Path(filename).suffix.casefold()
    if suffix in {".xlsx", ".xlsm", ".xls"}:
        frame = pd.read_excel(io.BytesIO(payload), dtype=object)
    elif suffix in {".tsv", ".tab"}:
        frame = pd.read_csv(io.BytesIO(payload), sep="\t", dtype=object)
    elif suffix in {".csv", ".txt"}:
        frame = pd.read_csv(io.BytesIO(payload), sep=None, engine="python", dtype=object)
    elif suffix in {".html", ".htm"}:
        frames = pd.read_html(io.StringIO(payload.decode("utf-8", errors="replace")))
        if not frames:
            raise ValueError(f"No tabular data found in {filename}.")
        frame = max(frames, key=len)
    else:
        raise ValueError(f"Unsupported source format '{suffix}'. Use CSV, TSV, XLSX or HTML.")
    frame = frame.dropna(how="all")
    if frame.empty:
        raise ValueError(f"No data rows found in {filename}.")
    frame.columns = [normalize_text(c) for c in frame.columns]
    return frame


def models_q_isin(isin):
    from django.db.models import Q
    return Q(isin_growth__iexact=isin) | Q(isin_dividend__iexact=isin)


def resolve_master_scheme(*, isin="", scheme_name=""):
    """Resolve ISIN first; unknown supplied ISIN does not fall back to name."""
    normalized_isin = normalize_isin(isin)
    if normalized_isin:
        matches = list(AMFIMasterScheme.objects.filter(is_active=True).filter(models_q_isin(normalized_isin))[:3])
        if len(matches) == 1:
            return matches[0], "isin"
        return None, "ambiguous" if len(matches) > 1 else "unmatched"
    key = normalize_key(scheme_name)
    if not key:
        return None, "unmatched"
    matches = []
    for scheme in AMFIMasterScheme.objects.filter(is_active=True).only("id", "scheme_name")[:20000]:
        if normalize_key(scheme.scheme_name) == key:
            matches.append(scheme)
            if len(matches) > 1:
                return None, "ambiguous"
    return (matches[0], "name_exact") if matches else (None, "unmatched")


def _scale_amount(value, column):
    if value is None:
        return None
    key = normalize_key(column)
    if "lakh" in key or "lac" in key:
        return value * Decimal("100000")
    if "crore" in key or " cr" in f" {key}":
        return value * Decimal("10000000")
    return value


def parse_ter_rows(payload, *, filename, as_of_date):
    frame = read_table(payload, filename)
    code = find_column(frame, ("scheme code", "amfi scheme code", "scheme_code"))
    isin = find_column(frame, ("isin", "isin code", "isin growth"))
    name = find_column(frame, ("scheme name", "scheme", "name of scheme", "scheme nav name"))
    plan = find_column(frame, ("plan", "plan type", "scheme plan"))
    ter = find_column(frame, ("base expense ratio", "ter", "expense ratio", "total expense ratio"))
    if not ter or not (code or isin or name):
        raise ValueError("TER file requires a TER column and scheme code, ISIN or exact scheme name.")
    rows = []
    for _, source in frame.iterrows():
        value = decimal_value(source.get(ter), percent=True)
        if value is not None:
            rows.append({
                "scheme_code": normalize_text(source.get(code)) if code else "",
                "isin": normalize_isin(source.get(isin)) if isin else "",
                "scheme_name": normalize_text(source.get(name)) if name else "",
                "plan": normalize_text(source.get(plan)) if plan else "",
                "ter_pct": value, "as_of_date": as_of_date,
            })
    return rows


def _instrument_type(raw_type, security_name):
    value = normalize_key(raw_type + " " + security_name)
    if any(x in value for x in ("cash", "treps", "tri party repo", "repo", "bank balance", "current account", "net current assets")):
        return "CASH"
    if any(x in value for x in ("equity", "shares", "stock", "listed equity")):
        return "EQUITY"
    if any(x in value for x in ("reit", "real estate investment trust")):
        return "REIT"
    if any(x in value for x in ("invit", "infrastructure investment trust")):
        return "INVIT"
    if any(x in value for x in ("government security", "g sec", "gsec", "bond", "debt", "fixed income", "debenture", "ncd", "certificate of deposit", "commercial paper", "treasury bill", "t bill", "state development loan")):
        return "BOND"
    if any(x in value for x in ("exchange traded fund", " etf", "mutual fund unit")):
        return "FUND"
    if any(x in value for x in ("future", "option", "derivative")):
        return "DERIVATIVE"
    if any(x in value for x in ("gold", "silver", "commodity")):
        return "COMMODITY"
    return "OTHER"


def parse_portfolio_rows(payload, *, filename, amc, portfolio_date):
    frame = read_table(payload, filename)
    name = find_column(frame, ("name of the instrument", "security name", "name of instrument", "instrument name", "security", "scrip name"))
    isin = find_column(frame, ("isin", "isin code", "isin no", "isin number"))
    pct = find_column(frame, ("% to net assets", "% of nav", "percentage of nav", "weight (%)", "weight", "portfolio percentage", "% nav"))
    if not name or not pct:
        raise ValueError("Portfolio file must include instrument name and % of NAV/weight.")
    scheme_code = find_column(frame, ("scheme code", "amfi scheme code"))
    scheme_isin = find_column(frame, ("scheme isin", "fund isin"))
    scheme_name = find_column(frame, ("scheme name", "fund name", "mutual fund scheme"))
    kind = find_column(frame, ("asset class", "instrument type", "asset type", "type"))
    sector = find_column(frame, ("sector", "industry"))
    quantity = find_column(frame, ("quantity", "qty", "units", "no of shares", "number of shares"))
    market_value = find_column(frame, ("market value", "fair value", "valuation"))
    rating = find_column(frame, ("credit rating", "rating", "instrument rating"))
    agency = find_column(frame, ("rating agency", "credit rating agency"))
    maturity = find_column(frame, ("maturity date", "date of maturity", "redemption date"))
    ytm = find_column(frame, ("ytm", "yield to maturity", "ytm (%)"))
    rows = []
    for _, source in frame.iterrows():
        security = normalize_text(source.get(name))
        weight = decimal_value(source.get(pct), percent=True)
        if not security or normalize_key(security) in {"total", "grand total", "net assets", "net current assets"} or weight is None or weight < 0:
            continue
        rows.append({
            "scheme_code": normalize_text(source.get(scheme_code)) if scheme_code else "",
            "scheme_isin": normalize_isin(source.get(scheme_isin)) if scheme_isin else "",
            "scheme_name": normalize_text(source.get(scheme_name)) if scheme_name else "",
            "security_name": security[:300], "isin": normalize_isin(source.get(isin)) if isin else "",
            "instrument_type": _instrument_type(normalize_text(source.get(kind)) if kind else "", security),
            "sector": normalize_text(source.get(sector))[:150] if sector else "",
            "industry": normalize_text(source.get(sector))[:150] if sector else "",
            "rating": normalize_text(source.get(rating))[:80] if rating else "",
            "rating_agency": normalize_text(source.get(agency))[:80] if agency else "",
            "quantity": decimal_value(source.get(quantity)) if quantity else None,
            "market_value": _scale_amount(decimal_value(source.get(market_value)), market_value) if market_value else None,
            "pct_of_nav": weight, "maturity_date": parse_date(source.get(maturity)) if maturity else None,
            "ytm_pct": decimal_value(source.get(ytm), percent=True) if ytm else None,
            "portfolio_date": portfolio_date, "amc": amc,
        })
    return rows


def parse_scheme_info_rows(payload, *, filename, as_of_date):
    frame = read_table(payload, filename)
    code = find_column(frame, ("scheme code", "amfi scheme code", "scheme_code"))
    isin = find_column(frame, ("isin", "isin growth"))
    name = find_column(frame, ("scheme name", "name of scheme"))
    if not (code or isin or name):
        raise ValueError("Scheme info file requires scheme code, ISIN or exact name.")
    aliases = {
        "benchmark_name": ("benchmark name", "benchmark index", "primary benchmark"),
        "inception_date": ("inception date", "allotment date", "scheme start date"),
        "fund_managers": ("fund manager", "fund managers", "manager name"),
        "riskometer": ("scheme riskometer", "riskometer", "scheme risk"),
        "benchmark_riskometer": ("benchmark riskometer", "benchmark risk"),
        "exit_load_text": ("exit load", "exit load details"),
        "min_lumpsum": ("minimum investment", "minimum lumpsum", "min lumpsum", "minimum application amount"),
        "min_sip": ("minimum sip", "sip minimum", "min sip"),
        "pe": ("portfolio pe", "p/e", "pe ratio"), "pb": ("portfolio pb", "p/b", "pb ratio"),
        "dividend_yield_pct": ("dividend yield", "portfolio dividend yield"),
        "turnover_ratio": ("portfolio turnover", "turnover ratio"),
        "ytm_pct": ("ytm", "yield to maturity"), "modified_duration": ("modified duration",),
        "macaulay_duration": ("macaulay duration",), "average_maturity": ("average maturity",),
    }
    columns = {field: find_column(frame, aliases) for field, aliases in aliases.items()}
    rows = []
    for _, source in frame.iterrows():
        row = {
            "scheme_code": normalize_text(source.get(code)) if code else "",
            "isin": normalize_isin(source.get(isin)) if isin else "",
            "scheme_name": normalize_text(source.get(name)) if name else "",
            "as_of_date": as_of_date,
        }
        for field, col in columns.items():
            if not col:
                continue
            value = source.get(col)
            if field == "inception_date":
                row[field] = parse_date(value)
            elif field == "fund_managers":
                row[field] = [normalize_text(x) for x in re.split(r"[;\n|]", normalize_text(value)) if normalize_text(x)]
            elif field in {"benchmark_name", "riskometer", "benchmark_riskometer", "exit_load_text"}:
                row[field] = normalize_text(value)
            else:
                row[field] = decimal_value(value, percent=field in {"dividend_yield_pct", "ytm_pct"})
        rows.append(row)
    return rows


def resolve_source_url(*, from_file="", supplied_source_url="", downloaded_url=""):
    """Require traceable official URL lineage for any locally supplied source file."""
    candidate = (supplied_source_url or downloaded_url or "").strip()
    if from_file and not candidate:
        raise ValueError("--source-url is required with --from-file.")
    if candidate:
        parsed = urlparse(candidate)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("--source-url must be an absolute HTTP(S) disclosure URL.")
    return candidate


def parse_aum_rows(payload, *, filename, as_of_date, amount_unit="AUTO"):
    frame = read_table(payload, filename)
    code = find_column(frame, ("scheme code", "amfi scheme code", "scheme_code"))
    isin = find_column(frame, ("isin", "isin growth"))
    name = find_column(frame, ("scheme name", "name of scheme", "scheme nav name"))
    aum = find_column(frame, ("month end aum", "aum", "net assets", "average aum", "asset size", "aum cr", "aum crore"))
    if not aum or not (code or isin or name):
        raise ValueError("AUM file requires AUM and scheme code, ISIN or exact name.")

    unit = str(amount_unit or "AUTO").strip().upper()
    if unit == "AUTO":
        header = normalize_key(aum)
        if "lakh" in header or "lac" in header:
            unit = "LAKH"
        elif "crore" in header or " cr" in f" {header}":
            unit = "CRORE"
        elif any(token in header.split() for token in ("rupee", "rupees", "rs", "inr")):
            unit = "RUPEES"
        else:
            raise ValueError(
                "AUM column does not declare rupees/lakh/crore; no unit will be guessed. "
                "Supply --amount-unit RUPEES, LAKH or CRORE."
            )
    if unit not in {"RUPEES", "LAKH", "CRORE"}:
        raise ValueError("--amount-unit must be AUTO, RUPEES, LAKH or CRORE.")

    rows = []
    for _, source in frame.iterrows():
        raw = decimal_value(source.get(aum))
        if raw is None:
            continue
        rupees = (
            raw if unit == "RUPEES"
            else raw * Decimal("100000") if unit == "LAKH"
            else raw * Decimal("10000000")
        )
        rows.append({
            "scheme_code": normalize_text(source.get(code)) if code else "",
            "isin": normalize_isin(source.get(isin)) if isin else "",
            "scheme_name": normalize_text(source.get(name)) if name else "",
            "aum_cr": (rupees / Decimal("10000000")).quantize(FOUR_DP),
            "as_of_date": as_of_date,
        })
    return rows

def parse_benchmark_tri_rows(payload, *, filename):
    frame = read_table(payload, filename)
    code = find_column(frame, ("index code", "benchmark code", "symbol", "index symbol"))
    name = find_column(frame, ("index name", "benchmark name", "index"))
    day = find_column(frame, ("date", "trade date", "timestamp"))
    tri = find_column(frame, ("tri", "total return index", "index value", "close"))
    if not (name or code) or not day or not tri:
        raise ValueError("Benchmark TRI file requires index name/code, date and TRI value.")
    rows = []
    for _, source in frame.iterrows():
        d, value = parse_date(source.get(day)), decimal_value(source.get(tri))
        if not d or value is None or value <= 0:
            continue
        rows.append({
            "index_code": normalize_text(source.get(code)) if code else normalize_key(source.get(name)).replace(" ", "_").upper(),
            "index_name": normalize_text(source.get(name)) if name else normalize_text(source.get(code)),
            "date": d, "tri_value": value,
        })
    return rows


def parse_market_cap_rows(payload, *, filename, effective_period):
    frame = read_table(payload, filename)
    isin = find_column(frame, ("isin", "isin code"))
    name = find_column(frame, ("company name", "security name", "name of company"))
    category = find_column(frame, ("category", "market cap category", "large mid small cap", "classification"))
    rank = find_column(frame, ("rank", "market cap rank"))
    if not isin or not category:
        raise ValueError("AMFI cap file requires ISIN and Large/Mid/Small cap category.")
    rows = []
    for _, source in frame.iterrows():
        code, cap = normalize_isin(source.get(isin)), normalize_key(source.get(category))
        if code and cap in {"large cap", "mid cap", "small cap"}:
            rank_value = decimal_value(source.get(rank)) if rank else None
            rows.append({
                "isin": code, "company_name": normalize_text(source.get(name)) if name else "",
                "market_cap_class": cap.replace(" ", "_").upper(),
                "market_cap_rank": int(rank_value) if rank_value is not None else None,
                "effective_period": effective_period,
            })
    return rows


@transaction.atomic
def persist_portfolio_rows(
    scheme, rows, *, portfolio_date, source, source_url="",
    portfolio_date_basis=MFMasterHolding.DATE_BASIS_DISCLOSURE,
):
    inserted = updated = skipped = 0
    for row in rows:
        name, isin = normalize_text(row.get("security_name")), normalize_isin(row.get("isin"))
        ticker = normalize_text(row.get("ticker"))[:40]
        if not name:
            skipped += 1
            continue
        key = (f"ISIN:{isin}" if isin else f"TICKER:{ticker}" if ticker else f"NAME:{normalize_key(name)}")[:320]
        exists = MFMasterHolding.objects.filter(
            scheme=scheme, portfolio_date=portfolio_date,
            portfolio_date_basis=portfolio_date_basis, security_key=key,
        ).exists()
        MFMasterHolding.objects.update_or_create(
            scheme=scheme, portfolio_date=portfolio_date,
            portfolio_date_basis=portfolio_date_basis, security_key=key,
            defaults={
                "security_name": name[:300], "isin": isin, "ticker": ticker,
                "portfolio_date_basis": portfolio_date_basis,
                "instrument_type": row.get("instrument_type") or "OTHER",
                "sector": row.get("sector") or "", "industry": row.get("industry") or "",
                "rating": row.get("rating") or "", "rating_agency": row.get("rating_agency") or "",
                "quantity": row.get("quantity"), "market_value": row.get("market_value"),
                "pct_of_nav": row.get("pct_of_nav"), "maturity_date": row.get("maturity_date"),
                "ytm_pct": row.get("ytm_pct"), "source": source, "source_url": source_url or "",
                "fetched_at": timezone.now(),
            },
        )
        updated += int(exists)
        inserted += int(not exists)
    return {"inserted": inserted, "updated": updated, "skipped": skipped}


@transaction.atomic
def upsert_masterdata_snapshot(scheme, *, as_of_date, values, source, source_url, field_names=None, quality_flags=None):
    snapshot, created = MFMasterData.objects.get_or_create(
        scheme=scheme, as_of_date=as_of_date,
        defaults={
            "scheme_code": scheme.scheme_code, "isin_growth": scheme.isin_growth or "",
            "isin_idcw": scheme.isin_dividend or "", "amc": scheme.amc_name or "",
            "scheme_name": scheme.scheme_name, "plan": scheme.plan or "", "option": scheme.option or "",
            "sebi_category_level_1": scheme.level_1_branch or "",
            "sebi_category_level_2": scheme.level_2_group or "",
            "sebi_category_level_3": scheme.level_3_category or "",
        },
    )
    valid = {field.name for field in MFMasterData._meta.fields}
    fields = field_names or [key for key in values if key in valid]
    lineage = dict(snapshot.field_sources or {})
    stamp = timezone.now().isoformat()
    for field in fields:
        if field in values and values[field] is not None:
            setattr(snapshot, field, values[field])
            lineage[field] = {"source": source, "source_url": source_url or "", "as_of_date": as_of_date.isoformat(), "fetched_at": stamp}
    snapshot.field_sources = lineage
    flags = list(snapshot.data_quality_flags or [])
    for flag in quality_flags or []:
        if flag not in flags:
            flags.append(flag)
    snapshot.data_quality_flags = flags
    snapshot.fetched_at = timezone.now()
    snapshot.save()
    return snapshot, created


def calculate_benchmark_relative_metrics(
    fund_nav, benchmark_nav, *, risk_free_rate=0.065, periods_per_year=252,
    minimum_window_years=3.0,
):
    """CAPM/active-risk ratios from aligned daily NAV/TRI.

    The default window is three years. At least 180 aligned observations per
    requested year are required; otherwise ratios remain null with a quality
    flag rather than annualizing a sparse or short series.
    """
    fund = {d: float(v) for d, v in fund_nav if float(v) > 0}
    bench = {d: float(v) for d, v in benchmark_nav if float(v) > 0}
    common = sorted(set(fund) & set(bench))
    years = (common[-1] - common[0]).days / 365.25 if len(common) >= 2 else 0.0
    n = max(0, len(common) - 1)
    empty = {
        "beta": None, "alpha_pct": None, "treynor": None, "r_squared": None,
        "tracking_error_pct": None, "information_ratio": None,
        "upside_capture_pct": None, "downside_capture_pct": None,
        "risk_free_rate_used_pct": risk_free_rate * 100,
        "ratio_window_years": years if common else None,
        "data_quality_flags": ["INSUFFICIENT_BENCHMARK_HISTORY"],
    }
    required_observations = max(4, int(max(0.0, minimum_window_years) * 180))
    if years < minimum_window_years or n < required_observations:
        return empty

    fund_returns = [fund[common[i]] / fund[common[i - 1]] - 1.0 for i in range(1, len(common))]
    benchmark_returns = [bench[common[i]] / bench[common[i - 1]] - 1.0 for i in range(1, len(common))]
    mean_fund = sum(fund_returns) / n
    mean_benchmark = sum(benchmark_returns) / n
    variance_benchmark = sum((value - mean_benchmark) ** 2 for value in benchmark_returns) / (n - 1)
    variance_fund = sum((value - mean_fund) ** 2 for value in fund_returns) / (n - 1)
    covariance = sum(
        (fund_returns[i] - mean_fund) * (benchmark_returns[i] - mean_benchmark)
        for i in range(n)
    ) / (n - 1)
    beta = covariance / variance_benchmark if variance_benchmark > 0 else None
    correlation = covariance / math.sqrt(variance_fund * variance_benchmark) if variance_fund > 0 and variance_benchmark > 0 else None
    daily_risk_free = (1 + risk_free_rate) ** (1 / periods_per_year) - 1
    alpha_daily = (
        (mean_fund - daily_risk_free) - beta * (mean_benchmark - daily_risk_free)
        if beta is not None else None
    )
    active_returns = [fund_returns[i] - benchmark_returns[i] for i in range(n)]
    mean_active = sum(active_returns) / n
    variance_active = sum((value - mean_active) ** 2 for value in active_returns) / (n - 1)

    def capture(upside):
        selected = [
            (fund_returns[i], benchmark_returns[i])
            for i in range(n)
            if (benchmark_returns[i] > 0 if upside else benchmark_returns[i] < 0)
        ]
        if not selected:
            return None
        fund_total = math.prod(1 + pair[0] for pair in selected) - 1
        benchmark_total = math.prod(1 + pair[1] for pair in selected) - 1
        return fund_total / benchmark_total * 100 if benchmark_total else None

    tracking_error = math.sqrt(variance_active) * math.sqrt(periods_per_year) * 100
    return {
        "beta": beta,
        "alpha_pct": alpha_daily * periods_per_year * 100 if alpha_daily is not None else None,
        "treynor": (
            (mean_fund - daily_risk_free) * periods_per_year * 100 / beta
            if beta not in (None, 0) else None
        ),
        "r_squared": correlation ** 2 if correlation is not None else None,
        "tracking_error_pct": tracking_error,
        "information_ratio": (
            mean_active / math.sqrt(variance_active) * math.sqrt(periods_per_year)
            if variance_active > 0 else None
        ),
        "upside_capture_pct": capture(True),
        "downside_capture_pct": capture(False),
        "risk_free_rate_used_pct": risk_free_rate * 100,
        "ratio_window_years": years,
        "data_quality_flags": [],
    }


def source_raw_directory(source, as_of_date):
    path = Path(settings.BASE_DIR) / "data" / "mf_raw" / re.sub(r"[^a-z0-9_-]", "_", source.casefold()) / as_of_date.isoformat()
    path.mkdir(parents=True, exist_ok=True)
    return path


def load_source_payload(*, source, as_of_date, from_file="", url=""):
    """Read a local file or directly download and cache a supplied source URL."""
    if from_file:
        path = Path(from_file).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(f"Input file does not exist: {path}")
        return path.read_bytes(), path.name, ""
    if not url:
        raise ValueError("Supply --from-file or a direct --url.")
    host = urlparse(url).netloc.casefold()
    request = requests.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/pdf,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,text/csv,*/*"})
    last_error = None
    for attempt in range(3):
        with _HOST_LOCK:
            wait = 1.0 - (time.monotonic() - _HOST_LAST_REQUEST.get(host, 0.0))
            if wait > 0:
                time.sleep(wait)
            _HOST_LAST_REQUEST[host] = time.monotonic()
        try:
            with requests.Session() as session:
                response = session.send(session.prepare_request(request), timeout=60)
                response.raise_for_status()
                payload = response.content
                ctype = response.headers.get("Content-Type", "").casefold()
                suffix = ".xlsx" if "spreadsheet" in ctype else ".csv" if "csv" in ctype else ".html"
                filename = Path(urlparse(response.url).path).name or f"download{suffix}"
                if not Path(filename).suffix:
                    filename += suffix
                raw = source_raw_directory(source, as_of_date) / filename
                temp = raw.with_suffix(raw.suffix + ".tmp")
                temp.write_bytes(payload)
                temp.replace(raw)
                return payload, filename, url
        except requests.RequestException as exc:
            last_error = exc
            if attempt < 2:
                time.sleep(2 ** (attempt + 1))
    raise RuntimeError(f"Unable to download {url}: {last_error}") from last_error


def start_source_run(*, source, command, as_of_date, source_url="", raw_file_path=""):
    return MFSourceRun.objects.create(
        source=source, command=command, as_of_date=as_of_date,
        source_url=source_url or "", raw_file_path=raw_file_path or "",
        status=MFSourceRun.Status.RUNNING,
    )


def finish_source_run(run, *, status, parsed=0, inserted=0, updated=0, skipped=0, unmatched=0, errors=None, metadata=None):
    run.completed_at = timezone.now()
    run.status = status
    run.rows_parsed, run.rows_inserted, run.rows_updated = parsed, inserted, updated
    run.rows_skipped, run.rows_unmatched = skipped, unmatched
    run.errors, run.metadata = errors or [], metadata or {}
    run.save(update_fields=["completed_at", "status", "rows_parsed", "rows_inserted", "rows_updated", "rows_skipped", "rows_unmatched", "errors", "metadata"])
    return run


def calculate_holdings_metrics(rows):
    allocation = {key: Decimal("0") for key in ("equity_pct", "debt_pct", "cash_pct", "others_pct")}
    ratings = {}
    total = weighted_ytm = ytm_weight = Decimal("0")
    ordered = sorted((r for r in rows if r.pct_of_nav is not None), key=lambda r: r.pct_of_nav, reverse=True)
    for row in ordered:
        weight = row.pct_of_nav
        total += weight
        kind = row.instrument_type.upper()
        bucket = "equity_pct" if kind == "EQUITY" else "debt_pct" if kind in {"BOND", "DEBT"} else "cash_pct" if kind == "CASH" else "others_pct"
        allocation[bucket] += weight
        if kind in {"BOND", "DEBT"} and row.rating:
            ratings[row.rating] = ratings.get(row.rating, Decimal("0")) + weight
        if kind in {"BOND", "DEBT"} and row.ytm_pct is not None:
            weighted_ytm += weight * row.ytm_pct
            ytm_weight += weight
    return {
        **{key: value.quantize(FOUR_DP) for key, value in allocation.items()},
        "number_of_holdings": Decimal(len(rows)).quantize(FOUR_DP),
        "top10_concentration_pct": sum((r.pct_of_nav or Decimal("0")) for r in ordered[:10]).quantize(FOUR_DP),
        "credit_rating_split": {k: str(v.quantize(FOUR_DP)) for k, v in ratings.items()},
        "ytm_pct": (weighted_ytm / ytm_weight).quantize(FOUR_DP) if ytm_weight else None,
        "reported_weight_pct": total.quantize(FOUR_DP),
        "data_quality_flags": ["PORTFOLIO_WEIGHT_NOT_100"] if total and abs(total - Decimal("100")) > Decimal("1.0") else [],
    }
