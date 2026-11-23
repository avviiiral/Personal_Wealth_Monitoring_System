"""Derived mutual-fund performance, benchmark risk and holdings analytics.

NAV-based metrics delegate to the established SEBI analytics implementation.
Benchmark-relative metrics use only same-date TRI observations and remain null
when the available history is insufficient.
"""
from datetime import date, timedelta
from decimal import Decimal

from django.db.models import Max

from mutual_funds.models import AMFIMasterNAV
from mutual_funds.master_models import (
    MFBenchmarkTRI, MFMarketCapClassification, MFMasterData, MFMasterHolding,
)
from mutual_funds.services.masterdata import (
    calculate_benchmark_relative_metrics, normalize_key,
)


def _subtract_months(value: date, months: int) -> date:
    month_index = value.year * 12 + value.month - 1 - months
    year, month = divmod(month_index, 12)
    month += 1
    import calendar
    day = min(value.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def _nav_on_or_before(series, target):
    candidates = [item for item in series if item[0] <= target]
    return candidates[-1] if candidates else None


def _simple_return(series, as_of, months):
    start = _nav_on_or_before(series, _subtract_months(as_of, months))
    end = _nav_on_or_before(series, as_of)
    if not start or not end or start[1] <= 0:
        return None
    return (float(end[1]) / float(start[1]) - 1.0) * 100.0


def _cap_split(holdings, equity_pct, as_of_date):
    result = {"large_cap_pct": Decimal("0"), "mid_cap_pct": Decimal("0"), "small_cap_pct": Decimal("0")}
    isin_values = {row.isin for row in holdings if row.isin and row.instrument_type == "EQUITY" and row.pct_of_nav is not None}
    if not isin_values:
        result["other_cap_pct"] = equity_pct
        return result
    expected_period = f"{as_of_date.year}-H{1 if as_of_date.month <= 6 else 2}"
    latest_period = (
        MFMarketCapClassification.objects.filter(effective_period__lte=expected_period)
        .order_by("-effective_period")
        .values_list("effective_period", flat=True).first()
    )
    classifications = {
        item.isin: item.market_cap_class
        for item in MFMarketCapClassification.objects.filter(
            isin__in=isin_values, effective_period=latest_period,
        )
    } if latest_period else {}
    for holding in holdings:
        if holding.instrument_type != "EQUITY" or not holding.isin or holding.pct_of_nav is None:
            continue
        cap_class = classifications.get(holding.isin)
        if cap_class == MFMarketCapClassification.LARGE_CAP:
            result["large_cap_pct"] += holding.pct_of_nav
        elif cap_class == MFMarketCapClassification.MID_CAP:
            result["mid_cap_pct"] += holding.pct_of_nav
        elif cap_class == MFMarketCapClassification.SMALL_CAP:
            result["small_cap_pct"] += holding.pct_of_nav
    assigned = sum(result.values(), Decimal("0"))
    result["other_cap_pct"] = max(Decimal("0"), (equity_pct or Decimal("0")) - assigned)
    return {key: value.quantize(Decimal("0.0001")) for key, value in result.items()}


def _holdings_metrics(scheme, as_of_date):
    latest_date = (
        MFMasterHolding.objects.filter(scheme=scheme, portfolio_date__lte=as_of_date)
        .aggregate(value=Max("portfolio_date"))["value"]
    )
    if latest_date is None:
        return {}, ["PORTFOLIO_NOT_AVAILABLE"], ""
    holdings = list(MFMasterHolding.objects.filter(scheme=scheme, portfolio_date=latest_date))
    allocation = {key: Decimal("0") for key in ("equity_pct", "debt_pct", "cash_pct", "others_pct")}
    sectors = {}
    ratings = {}
    ytm_total = Decimal("0")
    ytm_weight = Decimal("0")
    ranked = sorted((row for row in holdings if row.pct_of_nav is not None), key=lambda row: row.pct_of_nav, reverse=True)
    for row in ranked:
        weight = row.pct_of_nav
        kind = (row.instrument_type or "OTHER").upper()
        bucket = "equity_pct" if kind == "EQUITY" else "debt_pct" if kind in {"BOND", "DEBT"} else "cash_pct" if kind == "CASH" else "others_pct"
        allocation[bucket] += weight
        sector = (row.sector or row.industry or "").strip()
        if sector:
            sectors[sector] = sectors.get(sector, Decimal("0")) + weight
        if kind in {"BOND", "DEBT"} and row.rating:
            ratings[row.rating] = ratings.get(row.rating, Decimal("0")) + weight
        if kind in {"BOND", "DEBT"} and row.ytm_pct is not None:
            ytm_total += weight * row.ytm_pct
            ytm_weight += weight
    equity = allocation["equity_pct"]
    values = {
        **{key: value.quantize(Decimal("0.0001")) for key, value in allocation.items()},
        "number_of_holdings": Decimal(len(holdings)).quantize(Decimal("0.0001")),
        "top10_concentration_pct": sum((r.pct_of_nav or Decimal("0")) for r in ranked[:10]).quantize(Decimal("0.0001")),
        "credit_rating_split": {key: str(value.quantize(Decimal("0.0001"))) for key, value in ratings.items()},
        "sector_allocation": {key: str(value.quantize(Decimal("0.0001"))) for key, value in sectors.items()},
        "ytm_pct": (ytm_total / ytm_weight).quantize(Decimal("0.0001")) if ytm_weight else None,
    }
    values.update(_cap_split(holdings, equity, as_of_date))
    weights = sum(allocation.values(), Decimal("0"))
    flags = ["PORTFOLIO_WEIGHT_NOT_100"] if weights and abs(weights - Decimal("100")) > Decimal("1.0") else []
    source_url = next((row.source_url for row in holdings if row.source_url), "")
    return values, flags, source_url


def calculate_scheme_masterdata_metrics(
    scheme, as_of_date: date, *, risk_free_rate: float = 0.065,
    ratio_window_years: int = 3,
) -> dict:
    """Return calculated MFMasterData fields, quality flags and source URLs."""
    nav_rows = list(
        AMFIMasterNAV.objects.filter(scheme=scheme, date__lte=as_of_date)
        .order_by("date").values_list("date", "nav")
    )
    series = [(day, float(nav)) for day, nav in nav_rows if nav and float(nav) > 0]
    flags = []
    values = {}
    nav_source_url = "https://portal.amfiindia.com/spages/NAVAll.txt"
    if series:
        from mutual_funds.services.sebi_mf_analytics import compute_fund_performance_metrics
        metrics = compute_fund_performance_metrics(series, risk_free_rate=risk_free_rate)
        values.update({
            "return_1m_pct": _simple_return(series, as_of_date, 1),
            "return_3m_pct": _simple_return(series, as_of_date, 3),
            "return_6m_pct": _simple_return(series, as_of_date, 6),
            "return_1y_pct": metrics.get("CAGR_1Y_pct"),
            "return_3y_pct": metrics.get("CAGR_3Y_pct"),
            "return_5y_pct": metrics.get("CAGR_5Y_pct"),
            "return_10y_pct": metrics.get("CAGR_10Y_pct"),
            "std_dev_pct": metrics.get("Annualized_Volatility_pct"),
            "sharpe": metrics.get("Sharpe_Ratio"),
            "sortino": metrics.get("Sortino_Ratio"),
            "max_drawdown_pct": metrics.get("Max_Drawdown_pct"),
            "risk_free_rate_used_pct": risk_free_rate * 100,
        })
        elapsed_years = (series[-1][0] - series[0][0]).days / 365.25
        if elapsed_years > 1 and series[0][1] > 0:
            values["since_inception_cagr_pct"] = (
                (series[-1][1] / series[0][1]) ** (1 / elapsed_years) - 1
            ) * 100
        if elapsed_years < 1:
            flags.append("INSUFFICIENT_NAV_HISTORY_FOR_1Y")
    else:
        flags.append("NAV_HISTORY_NOT_AVAILABLE")

    previous = (
        MFMasterData.objects.filter(scheme=scheme, as_of_date__lte=as_of_date)
        .exclude(benchmark_name="").order_by("-as_of_date").first()
    )
    benchmark_name = previous.benchmark_name if previous else ""
    benchmark_rows = []
    benchmark_source_url = ""
    if benchmark_name and series:
        candidates = list(MFBenchmarkTRI.objects.filter(date__lte=as_of_date).order_by("-date"))
        matching_codes = {
            row.index_code for row in candidates
            if normalize_key(row.index_name) == normalize_key(benchmark_name)
        }
        if len(matching_codes) == 1:
            code = next(iter(matching_codes))
            start_date = max(series[0][0], as_of_date - timedelta(days=round(ratio_window_years * 365.25)))
            benchmark_rows = list(
                MFBenchmarkTRI.objects.filter(index_code=code, date__gte=start_date, date__lte=as_of_date)
                .order_by("date").values_list("date", "tri_value")
            )
            benchmark_source_url = (
                MFBenchmarkTRI.objects.filter(index_code=code, date__lte=as_of_date)
                .order_by("-date").values_list("source_url", flat=True).first() or ""
            )
    if benchmark_rows and series:
        start_date = max(series[0][0], benchmark_rows[0][0])
        fund_window = [(d, nav) for d, nav in series if d >= start_date]
        ratios = calculate_benchmark_relative_metrics(
            fund_window, [(d, float(value)) for d, value in benchmark_rows],
            risk_free_rate=risk_free_rate,
            minimum_window_years=ratio_window_years,
        )
        for key in (
            "beta", "alpha_pct", "treynor", "r_squared", "tracking_error_pct",
            "information_ratio", "upside_capture_pct", "downside_capture_pct",
            "risk_free_rate_used_pct", "ratio_window_years",
        ):
            field = {
                "alpha_pct": "alpha_pct",
            }.get(key, key)
            values[field] = ratios.get(key)
        flags.extend(ratios.get("data_quality_flags", []))
    else:
        values.update({
            "beta": None, "alpha_pct": None, "treynor": None, "r_squared": None,
            "tracking_error_pct": None, "information_ratio": None,
            "upside_capture_pct": None, "downside_capture_pct": None,
            "ratio_window_years": None,
        })
        flags.append("BENCHMARK_TRI_NOT_AVAILABLE" if benchmark_name else "BENCHMARK_NAME_NOT_AVAILABLE")

    holding_values, holding_flags, portfolio_source_url = _holdings_metrics(scheme, as_of_date)
    values.update(holding_values)
    flags.extend(holding_flags)
    source_urls = {
        "nav": nav_source_url,
        "benchmark": benchmark_source_url,
        "portfolio": portfolio_source_url,
    }
    return {
        "values": values,
        "data_quality_flags": sorted(set(flags)),
        "source_urls": source_urls,
        "nav_observations": len(series),
    }
