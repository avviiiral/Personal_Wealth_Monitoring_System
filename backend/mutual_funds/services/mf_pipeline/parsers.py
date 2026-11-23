"""Pure parsers for the masterdata pipeline's sources.

Field names were taken from live responses captured on 23-Nov-2026 (see
tests/fixtures/mf_pipeline). Note that several AMFI feeds differ from the
amfipy package's documentation: the risk-parameter feed carries no ISIN,
scheme code or Sharpe ratio, and the TER feed identifies the parent scheme by
NSDL scheme code rather than ISIN.
"""
from __future__ import annotations

import calendar
import csv
import io
import re
from datetime import date, datetime
from decimal import Decimal

from mutual_funds.services.masterdata import (
    decimal_value, find_column, normalize_key, normalize_text, parse_date,
    read_table,
)

ISIN_RE = re.compile(r"[A-Z]{2}[A-Z0-9]{9}[0-9]")
MONTHS = {name.casefold(): index for index, name in enumerate(calendar.month_name) if name}


def normalize_parent_name(value) -> str:
    """Exact-match key: case, whitespace, punctuation and '&'/'and' only."""
    return normalize_key(value)


def _date(value):
    """parse_date, but treat a leading ISO date (e.g. '2026-10-31 00:00:00') strictly."""
    if isinstance(value, (datetime, date)):
        return value.date() if isinstance(value, datetime) else value
    text = normalize_text(value)
    match = re.match(r"^(\d{4}-\d{2}-\d{2})", text)
    if match:
        try:
            return date.fromisoformat(match.group(1))
        except ValueError:
            return None
    return parse_date(text) if text else None


def _records(payload):
    if isinstance(payload, dict):
        payload = payload.get("data", [])
    return payload if isinstance(payload, list) else []


def _number(value, percent=False):
    if value is None:
        return None
    return decimal_value(str(value), percent=percent)


# --------------------------------------------------------------------------
# AMFI scheme data (portal.amfiindia.com/DownloadSchemeData_Po.aspx?mf=0)
# --------------------------------------------------------------------------
def parse_scheme_data_csv(text: str) -> list[dict]:
    reader = csv.reader(io.StringIO(text.lstrip("﻿")))
    header = next(reader, None)
    if not header:
        return []
    keys = [normalize_key(col) for col in header]

    def index(*aliases):
        for alias in aliases:
            if alias in keys:
                return keys.index(alias)
        return None

    i_amc, i_code, i_name = index("amc"), index("code"), index("scheme name")
    i_category, i_nav_name = index("scheme category"), index("scheme nav name")
    i_launch = index("launch date")
    if i_code is None or i_name is None:
        raise ValueError("AMFI scheme data must contain Code and Scheme Name columns.")
    rows = []
    for cells in reader:
        if len(cells) <= max(i_code, i_name):
            continue
        code = normalize_text(cells[i_code])
        if not code.isdigit():
            continue
        isin_text = normalize_text(cells[-1]).upper().replace(" ", "")
        rows.append({
            "amfi_code": code,
            "amc": normalize_text(cells[i_amc]) if i_amc is not None else "",
            "parent_name": normalize_text(cells[i_name]),
            "category": normalize_text(cells[i_category]) if i_category is not None else "",
            "nav_name": normalize_text(cells[i_nav_name]) if i_nav_name is not None else "",
            "launch_date": _date(cells[i_launch]) if i_launch is not None and i_launch < len(cells) else None,
            "isins": ISIN_RE.findall(isin_text),
        })
    return rows


# --------------------------------------------------------------------------
# AMFI TER export (amfipy client.ter.download_excel)
# --------------------------------------------------------------------------
def parse_ter_export(payload: bytes, filename: str) -> list[dict]:
    frame = read_table(payload, filename)
    code = find_column(frame, ("nsdl scheme code",))
    name = find_column(frame, ("scheme name",))
    category = find_column(frame, ("scheme category",))
    day = find_column(frame, ("ter date",))
    regular = find_column(frame, ("regular plan total ter",))
    direct = find_column(frame, ("direct plan total ter",))
    if not (code and name and day) or not (regular or direct):
        raise ValueError("TER export must include NSDL Scheme Code, Scheme Name, TER Date and Total TER columns.")
    latest = {}
    for _, source in frame.iterrows():
        nsdl = normalize_text(source.get(code))
        ter_date = _date(source.get(day))
        if not nsdl or ter_date is None:
            continue  # footnotes and blank rows
        row = {
            "nsdl_code": nsdl,
            "parent_name": normalize_text(source.get(name)),
            "category": normalize_text(source.get(category)) if category else "",
            "ter_date": ter_date,
            "regular_ter_pct": decimal_value(source.get(regular), percent=True) if regular else None,
            "direct_ter_pct": decimal_value(source.get(direct), percent=True) if direct else None,
        }
        current = latest.get(nsdl)
        if current is None or ter_date >= current["ter_date"]:
            latest[nsdl] = row
    return list(latest.values())


# --------------------------------------------------------------------------
# AMFI Disclosure of Risk Parameters (amfipy client.risk_parameters.fetch)
# --------------------------------------------------------------------------
def parse_risk_parameters(payload, *, category_id) -> list[dict]:
    rows = []
    for record in _records(payload):
        if not isinstance(record, dict) or record.get("SchemeID") in (None, ""):
            continue
        report_month = _date(record.get("date"))
        if report_month is None:
            continue
        volatility = record.get("volatility") or {}
        valuation = record.get("valuation") or {}
        rows.append({
            "risk_scheme_id": str(record["SchemeID"]),
            "report_month": report_month.replace(day=1),
            "category_id": str(record.get("Cat_ID") or category_id),
            "mf_id": str(record.get("MF_ID") or ""),
            "amc_name": normalize_text(record.get("MF_Name")),
            "scheme_name": normalize_text(record.get("SchemeName")),
            "aum_cr": _number(record.get("AUM")),
            "portfolio_std_dev_pct": _number(volatility.get("Volatility_PortfolioASD")),
            "benchmark_std_dev_pct": _number(volatility.get("Volatility_BenchmarkASD")),
            "portfolio_beta": _number(volatility.get("Volatility_PortfolioBeta")),
            "portfolio_pe": _number(valuation.get("Valuation_PortfolioTrailing12mPE")),
            "portfolio_turnover_ratio": _number(valuation.get("Valuation_PortfolioTurnoverRatio")),
            "raw": record,
        })
    return rows


# --------------------------------------------------------------------------
# AMFI scheme-wise average AUM (amfipy client.aum.*)
# --------------------------------------------------------------------------
def quarter_bounds(label: str) -> tuple[date, date]:
    match = re.match(r"^\s*([A-Za-z]+)\s*-\s*([A-Za-z]+)\s+(\d{4})\s*$", label or "")
    if not match or match.group(1).casefold() not in MONTHS or match.group(2).casefold() not in MONTHS:
        raise ValueError(f"Unrecognised AMFI AAUM period label: {label!r}")
    start_month, end_month, year = MONTHS[match.group(1).casefold()], MONTHS[match.group(2).casefold()], int(match.group(3))
    start_year = year - 1 if start_month > end_month else year
    end = date(year, end_month, calendar.monthrange(year, end_month)[1])
    return date(start_year, start_month, 1), end


def parse_aaum_periods(payload) -> list[dict]:
    data = payload.get("data", payload) if isinstance(payload, dict) else {}
    periods = data.get("periods", []) if isinstance(data, dict) else []
    rows = []
    for period in periods:
        try:
            start, end = quarter_bounds(period.get("period", ""))
        except ValueError:
            continue
        rows.append({"id": period.get("id"), "label": period["period"], "quarter_start": start, "quarter_end": end})
    return rows


def parse_aaum_schemewise(payload) -> list[dict]:
    rows = []
    for category in _records(payload):
        category_name = normalize_text(category.get("SchemeCat_Desc"))
        is_domestic_fof = any(token in category_name.casefold() for token in ("fof domestic", "fund of funds scheme (domestic)"))
        for scheme in category.get("schemes") or []:
            code = normalize_text(scheme.get("AMFI_Code"))
            if not code.isdigit():
                continue
            amounts = scheme.get("AverageAumForTheMonth") or {}
            main = _number(amounts.get("ExcludingFundOfFundsDomesticButIncludingFundOfFundsOverseas"))
            fof = _number(amounts.get("FundOfFundsDomestic"))
            chosen, other = (fof, main) if is_domestic_fof else (main, fof)
            if (chosen is None or chosen == 0) and other is not None and other > 0:
                chosen = other
            if chosen is None:
                continue
            rows.append({
                "amfi_code": code,
                "nav_name": normalize_text(scheme.get("SchemeNAVName")),
                "category": category_name,
                "amount_lakh": chosen,
            })
    return rows


# --------------------------------------------------------------------------
# AMFI tracking error (amfipy client.tracking.error)
# --------------------------------------------------------------------------
def parse_tracking_error(payload) -> list[dict]:
    rows = []
    for record in _records(payload):
        name = normalize_text(record.get("Scheme_Name"))
        as_of = _date(record.get("date"))
        if not name or as_of is None:
            continue
        rows.append({
            "parent_name": name,
            "benchmark": normalize_text(record.get("Benchmark")),
            "regular_pct": _number(record.get("RegularPercent")),
            "direct_pct": _number(record.get("DirectPercent")),
            "mf_id": str(record.get("mfId") or ""),
            "as_of": as_of,
        })
    return rows


# --------------------------------------------------------------------------
# mfapi.in
# --------------------------------------------------------------------------
def parse_mfapi_payload(payload) -> dict:
    meta = (payload or {}).get("meta") or {}
    navs = {}
    for row in (payload or {}).get("data") or []:
        try:
            day = datetime.strptime(str(row.get("date")), "%d-%m-%Y").date()
        except (TypeError, ValueError):
            continue
        try:
            value = Decimal(str(row.get("nav")).strip())
        except Exception:
            continue
        if value.is_finite() and value > 0:
            navs[day] = value
    return {
        "scheme_code": str(meta.get("scheme_code") or ""),
        "isin_growth": normalize_text(meta.get("isin_growth")).upper(),
        "isin_reinvestment": normalize_text(meta.get("isin_div_reinvestment")).upper(),
        "navs": sorted(navs.items()),
    }


# --------------------------------------------------------------------------
# Risk-free rate table (operator-supplied CSV/XLSX or configured URL)
# --------------------------------------------------------------------------
def parse_risk_free_table(payload: bytes, filename: str) -> list[dict]:
    frame = read_table(payload, filename)
    day = find_column(frame, ("date", "auction date", "as of date", "as on"))
    rate = None
    for column in frame.columns:
        key = normalize_key(column)
        if column != day and any(token in key for token in ("yield", "rate", "t bill", "tbill", "cut off")):
            rate = column
            break
    if not day or not rate:
        raise ValueError("Risk-free file needs a date column and a yield/rate column.")
    rows = {}
    for _, source in frame.iterrows():
        when = _date(source.get(day))
        value = decimal_value(source.get(rate), percent=True)
        if when is not None and value is not None and value > 0:
            rows[when] = value
    return [{"date": d, "rate_pct": v} for d, v in sorted(rows.items())]
