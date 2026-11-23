"""Trailing risk / return ratios for mutual-fund NAV series.

Pure functions only (no database access) so the maths can be unit-tested in
isolation. Conventions:

* inputs are ``[(date, value), ...]`` daily series (NAV or total-return index);
* windows are trailing 1Y and 3Y calendar windows ending on ``as_of``;
* returns are simple daily returns between consecutive observations;
* volatility is the sample standard deviation x sqrt(252);
* Rp / Rm are the annualised (CAGR) returns over the window;
* Sharpe  = (Rp - Rf) / sigma_annual
* Sortino = (Rp - Rf) / downside deviation (daily shortfall below the daily
  risk-free rate, annualised by sqrt(252))
* Beta    = cov(fund, benchmark) / var(benchmark) on aligned dates
* Alpha   = Rp - [Rf + beta x (Rm - Rf)]   (Jensen's alpha, in %)

Every output is a Decimal rounded to 4 places, or None when the inputs are
insufficient. None is never replaced by zero.
"""
from __future__ import annotations

import math
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Iterable, Sequence

TRADING_DAYS = 252
FOUR_DP = Decimal("0.0001")
WINDOWS = ((1, "1y"), (3, "3y"))
# A window is usable when its first observation is within this many days of
# the window start and it holds at least MIN_COVERAGE of the expected number
# of daily returns.
MAX_START_GAP_DAYS = 10
MIN_COVERAGE = 0.8


def to_decimal(value: float | None) -> Decimal | None:
    if value is None or not math.isfinite(value):
        return None
    return Decimal(str(value)).quantize(FOUR_DP, rounding=ROUND_HALF_UP)


def window_start(as_of: date, years: int) -> date:
    try:
        return as_of.replace(year=as_of.year - years)
    except ValueError:  # 29-Feb
        return as_of.replace(year=as_of.year - years, day=28)


def _clean(series: Iterable[tuple[date, float]] | None) -> list[tuple[date, float]]:
    by_date = {}
    for day, value in series or []:
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(number) and number > 0:
            by_date[day] = number
    return sorted(by_date.items())


def _window(series: Sequence[tuple[date, float]], as_of: date, years: int):
    start = window_start(as_of, years)
    points = [(d, v) for d, v in series if start <= d <= as_of]
    if len(points) < 2:
        return None
    if points[0][0] > start + timedelta(days=MAX_START_GAP_DAYS):
        return None
    if len(points) - 1 < MIN_COVERAGE * TRADING_DAYS * years:
        return None
    return points


def _returns(points):
    return [points[i][1] / points[i - 1][1] - 1.0 for i in range(1, len(points))]


def _cagr(points) -> float | None:
    elapsed = (points[-1][0] - points[0][0]).days / 365.25
    if elapsed <= 0:
        return None
    return (points[-1][1] / points[0][1]) ** (1.0 / elapsed) - 1.0


def _sample_std(values) -> float | None:
    n = len(values)
    if n < 2:
        return None
    mean = sum(values) / n
    return math.sqrt(sum((v - mean) ** 2 for v in values) / (n - 1))


def capm_alpha(*, rp: float, rf: float, beta: float, rm: float) -> float:
    """Jensen's alpha: Rp - [Rf + beta x (Rm - Rf)] (all annual decimals)."""
    return rp - (rf + beta * (rm - rf))


def beta_of(fund_points, bench_points) -> tuple[float | None, list]:
    """Beta on dates common to both series; returns (beta, aligned bench points)."""
    bench = dict(bench_points)
    common = [(d, v) for d, v in fund_points if d in bench]
    if len(common) < 2:
        return None, []
    aligned_bench = [(d, bench[d]) for d, _ in common]
    fr, br = _returns(common), _returns(aligned_bench)
    n = len(br)
    mean_f, mean_b = sum(fr) / n, sum(br) / n
    var_b = sum((b - mean_b) ** 2 for b in br) / (n - 1) if n > 1 else 0.0
    if var_b <= 0:
        return None, aligned_bench
    cov = sum((fr[i] - mean_f) * (br[i] - mean_b) for i in range(n)) / (n - 1)
    return cov / var_b, aligned_bench


def window_ratios(fund_series, bench_series, *, as_of: date, years: int, risk_free_rate: float) -> dict:
    """Ratios for one trailing window. ``risk_free_rate`` is an annual decimal."""
    empty = {"std_dev_pct": None, "sharpe": None, "sortino": None, "beta": None, "alpha_pct": None}
    flags = []
    points = _window(fund_series, as_of, years)
    if points is None:
        return {**empty, "flags": [f"INSUFFICIENT_HISTORY_{years}Y"], "observations": 0}

    rets = _returns(points)
    sd_daily = _sample_std(rets)
    sigma = sd_daily * math.sqrt(TRADING_DAYS) if sd_daily is not None else None
    rp = _cagr(points)
    rf_daily = (1.0 + risk_free_rate) ** (1.0 / TRADING_DAYS) - 1.0
    downside = math.sqrt(sum(min(r - rf_daily, 0.0) ** 2 for r in rets) / len(rets)) * math.sqrt(TRADING_DAYS)

    result = dict(empty)
    result["std_dev_pct"] = sigma * 100 if sigma is not None else None
    if rp is not None and sigma:
        result["sharpe"] = (rp - risk_free_rate) / sigma
    if rp is not None and downside > 0:
        result["sortino"] = (rp - risk_free_rate) / downside

    if bench_series:
        bench_points = _window(bench_series, as_of, years)
        beta, aligned = beta_of(points, bench_points) if bench_points else (None, [])
        if beta is not None and len(aligned) - 1 >= MIN_COVERAGE * TRADING_DAYS * years:
            rm = _cagr(aligned)
            result["beta"] = beta
            if rp is not None and rm is not None:
                result["alpha_pct"] = capm_alpha(rp=rp, rf=risk_free_rate, beta=beta, rm=rm) * 100
        elif bench_points is None or len(aligned) - 1 < MIN_COVERAGE * TRADING_DAYS * years:
            flags.append(f"INSUFFICIENT_BENCHMARK_HISTORY_{years}Y")
    return {**result, "flags": flags, "observations": len(rets)}


def compute_ratio_set(fund_series, bench_series, *, as_of: date, risk_free_rate_pct) -> dict:
    """Return the calc_* fields for mf_masterdata (1Y and 3Y) plus flags."""
    rf_pct = Decimal(str(risk_free_rate_pct))
    rf = float(rf_pct) / 100.0
    fund = _clean(fund_series)
    bench = _clean(bench_series) if bench_series else []
    output = {"calc_risk_free_rate_pct": rf_pct.quantize(FOUR_DP), "flags": []}
    if not bench:
        output["flags"].append("BENCHMARK_NOT_AVAILABLE")
    for years, suffix in WINDOWS:
        ratios = window_ratios(fund, bench, as_of=as_of, years=years, risk_free_rate=rf)
        output[f"calc_std_dev_{suffix}_pct"] = to_decimal(ratios["std_dev_pct"])
        output[f"calc_sharpe_{suffix}"] = to_decimal(ratios["sharpe"])
        output[f"calc_sortino_{suffix}"] = to_decimal(ratios["sortino"])
        output[f"calc_beta_{suffix}"] = to_decimal(ratios["beta"])
        output[f"calc_alpha_{suffix}_pct"] = to_decimal(ratios["alpha_pct"])
        output["flags"].extend(ratios["flags"])
    output["flags"] = sorted(set(output["flags"]))
    return output
