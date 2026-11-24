"""Server-side analytics for the Mutual Fund Intelligence page.

The functions here only read stored data. Every number they publish is
either a stored API value, a value computed from stored NAV/holdings, or
a peer statistic (average, percentile) over the funds being displayed.
"""

from collections import Counter, defaultdict
from decimal import Decimal
from statistics import mean

from django.db.models import Count, Max, Q
from django.utils import timezone

from mf_intelligence.constants import (
    CAP_BUCKETS,
    DEFAULT_BENCHMARK_BY_ASSET_CLASS,
    DEFAULT_BENCHMARK_BY_CATEGORY,
    FALLBACK_BENCHMARK,
    HORIZONS,
    METRIC_FIELDS,
    PLAN_CHOICES,
    QUANT_SCORE_COMPONENTS,
    QUANT_SCORE_MIN_COMPONENTS,
    RETURN_PERIODS,
    STYLE_BUCKETS,
    SUMMARY_METRIC_FIELDS,
)
from mf_intelligence.models import (
    MFAumHistory,
    MFBenchmark,
    MFBenchmarkMonthly,
    MFCategory,
    MFCategoryMedian,
    MFHolding,
    MFHoldingSnapshot,
    MFNavMonthly,
    MFScheme,
    MFSchemeMetric,
    MFSyncRun,
)
from mf_intelligence.services import cap_list
from mf_intelligence.services.classification import (
    CASH_LIKE,
    CORPORATE,
    SOVEREIGN,
    fixed_income_issuer_type,
    fund_match_key,
    is_cash,
    is_equity,
    is_fixed_income,
    maturity_bucket,
    maturity_from_name,
    sector_style,
    short_fund_name,
)
from mf_intelligence.services.universe import is_single_plan_category, peer_schemes


RETURN_KEYS = [suffix for suffix, *_ in RETURN_PERIODS] + ["ytd", "si"]
CALENDAR_YEARS_SHOWN = 5
UNCLASSIFIED = "Unclassified"


def num(value, places=4):
    if value is None:
        return None
    if isinstance(value, Decimal):
        value = float(value)
    try:
        return round(float(value), places)
    except (TypeError, ValueError):
        return None


def avg(values):
    clean = [value for value in values if value is not None]
    return round(mean(clean), 4) if clean else None


def percentile_ranks(values_by_key, higher_is_better=True):
    """Mid-rank percentile (0-100) of each value within the group."""
    clean = {key: value for key, value in values_by_key.items() if value is not None}
    count = len(clean)
    if not count:
        return {}
    ordered = sorted(clean.values())
    ranks = {}
    for key, value in clean.items():
        below = sum(1 for other in ordered if other < value)
        equal = sum(1 for other in ordered if other == value)
        pct = 50.0 if count == 1 else (below + 0.5 * equal) / count * 100
        ranks[key] = round(pct if higher_is_better else 100 - pct, 4)
    return ranks


def default_benchmark_slug(category):
    return (
        DEFAULT_BENCHMARK_BY_CATEGORY.get(category.slug)
        or DEFAULT_BENCHMARK_BY_ASSET_CLASS.get(category.asset_class)
        or FALLBACK_BENCHMARK
    )


def benchmark_payload(benchmark):
    if benchmark is None:
        return None
    returns = {suffix: num(getattr(benchmark, f"return_{suffix}")) for suffix, *_ in RETURN_PERIODS}
    returns["ytd"] = num(benchmark.return_ytd)
    return {
        "slug": benchmark.slug,
        "name": benchmark.name,
        "asset_type": benchmark.asset_type,
        "latest_date": benchmark.latest_date,
        "returns": returns,
        "calendar": {year: num(value) for year, value in (benchmark.calendar_returns or {}).items()},
    }


def resolve_benchmark(category, requested_slug=None):
    slugs = [slug for slug in (requested_slug, default_benchmark_slug(category), FALLBACK_BENCHMARK) if slug]
    benchmarks = {item.slug: item for item in MFBenchmark.objects.filter(slug__in=slugs)}
    for slug in slugs:
        if slug in benchmarks and benchmarks[slug].latest_date:
            return benchmarks[slug]
    return None


def _scheme_returns(scheme):
    returns = {suffix: num(getattr(scheme, f"return_{suffix}")) for suffix, *_ in RETURN_PERIODS}
    returns["ytd"] = num(scheme.return_ytd)
    returns["si"] = num(scheme.return_since_inception)
    return returns


def _metrics_by_scheme(scheme_ids, full_horizon=None):
    """scheme_id -> horizon -> metrics.

    With ``full_horizon`` only that horizon carries every field; the others
    carry SUMMARY_METRIC_FIELDS, which keeps large categories light.
    """
    metrics = defaultdict(dict)
    for metric in MFSchemeMetric.objects.filter(scheme_id__in=scheme_ids):
        fields = METRIC_FIELDS if full_horizon in (None, metric.horizon) else SUMMARY_METRIC_FIELDS
        metrics[metric.scheme_id][metric.horizon] = {field: num(getattr(metric, field)) for field in fields}
    return metrics


def _calmar(metrics):
    cagr = metrics.get("trailing_cagr")
    drawdown = metrics.get("trailing_max_drawdown")
    if cagr is None or drawdown in (None, 0):
        return None
    return round(cagr / abs(drawdown), 4)


def latest_snapshots(scheme_ids):
    latest = {}
    for snapshot in MFHoldingSnapshot.objects.filter(scheme_id__in=scheme_ids).order_by("scheme_id", "-as_of"):
        latest.setdefault(snapshot.scheme_id, snapshot)
    return latest


def _sibling_key(scheme):
    return (scheme.category_id, (scheme.amc or "").strip().lower(), fund_match_key(scheme.name))


def snapshots_with_sibling_fallback(schemes):
    """Latest snapshot per scheme, borrowing a sibling plan's disclosure.

    A portfolio disclosure covers the whole fund, so the Regular and
    Direct growth plans of one fund share holdings. When a scheme has no
    stored disclosure, the same fund's other plan (same category, AMC and
    name without plan/option words, see fund_match_key) supplies it. Returns
    (scheme_id -> snapshot, scheme_id -> amfi_code of the sibling used).
    """
    snapshots = latest_snapshots([scheme.pk for scheme in schemes])
    missing = [scheme for scheme in schemes if scheme.pk not in snapshots]
    borrowed = {}
    if not missing:
        return snapshots, borrowed
    category_ids = {scheme.category_id for scheme in missing}
    candidates = MFScheme.objects.filter(
        category_id__in=category_ids,
        holdings_synced_at__isnull=False,
        option__in=["Growth", "Other"],
    ).exclude(pk__in=[scheme.pk for scheme in schemes if scheme.pk in snapshots])
    by_key = {}
    for candidate in candidates:
        by_key.setdefault(_sibling_key(candidate), candidate)
    wanted = {scheme.pk: by_key.get(_sibling_key(scheme)) for scheme in missing}
    sibling_snapshots = latest_snapshots([sibling.pk for sibling in wanted.values() if sibling])
    for scheme_id, sibling in wanted.items():
        if sibling and sibling.pk in sibling_snapshots:
            snapshots[scheme_id] = sibling_snapshots[sibling.pk]
            borrowed[scheme_id] = sibling.amfi_code
    return snapshots, borrowed


def _holding_rows(snapshot_ids):
    rows = defaultdict(list)
    fields = (
        "snapshot_id", "isin", "name", "industry", "holding_type", "holding_pct",
        "prev_holding_pct", "change_pct", "status", "is_exit",
    )
    for values in MFHolding.objects.filter(snapshot_id__in=snapshot_ids).values_list(*fields):
        row = dict(zip(fields, values))
        row["holding_pct"] = num(row["holding_pct"])
        row["prev_holding_pct"] = num(row["prev_holding_pct"])
        row["change_pct"] = num(row["change_pct"])
        rows[row["snapshot_id"]].append(row)
    return rows


def cap_bucket(isin, lookup, method):
    entry = lookup.get(isin)
    if entry:
        return entry[0]
    if method == "INDEX_PROXY" and (isin or "").startswith("INE"):
        # Index-membership proxy: not in NIFTY 100 / Midcap 150 => small.
        return "Small Cap"
    return UNCLASSIFIED


def _rebased(weights, total, buckets):
    if not total:
        return {bucket: None for bucket in buckets}
    return {bucket: round(weights.get(bucket, 0.0) / total * 100, 4) for bucket in buckets}


def summarise_holdings(snapshot, rows, cap_lookup, cap_method):
    if snapshot is None:
        return None
    live = [row for row in rows if not row["is_exit"]]
    equity = [row for row in live if is_equity(row["holding_type"]) and (row["holding_pct"] or 0) > 0]
    fixed_income = [row for row in live if is_fixed_income(row["holding_type"]) and (row["holding_pct"] or 0) > 0]
    non_cash = sorted(
        (row for row in live if not is_cash(row["holding_type"]) and (row["holding_pct"] or 0) > 0),
        key=lambda row: row["holding_pct"],
        reverse=True,
    )
    equity_weight = sum(row["holding_pct"] for row in equity)

    sectors = defaultdict(float)
    styles = defaultdict(float)
    caps = defaultdict(float)
    for row in equity:
        industry = row["industry"] or "Unspecified"
        sectors[industry] += row["holding_pct"]
        styles[sector_style(industry)] += row["holding_pct"]
        caps[cap_bucket(row["isin"], cap_lookup, cap_method)] += row["holding_pct"]

    fixed_income_summary = None
    if fixed_income:
        fi_weight = sum(row["holding_pct"] for row in fixed_income)
        issuer = defaultdict(float)
        buckets = defaultdict(float)
        weighted_years, dated_weight = 0.0, 0.0
        money_market = 0.0
        for row in fixed_income:
            issuer[fixed_income_issuer_type(row["isin"], row["name"])] += row["holding_pct"]
            if row["holding_type"] == "money_market":
                money_market += row["holding_pct"]
            maturity = maturity_from_name(row["name"])
            if maturity:
                years = max((maturity - snapshot.as_of).days / 365.25, 0.0)
                weighted_years += years * row["holding_pct"]
                dated_weight += row["holding_pct"]
                buckets[maturity_bucket(years)] += row["holding_pct"]
        fixed_income_summary = {
            "weight_pct": round(fi_weight, 4),
            "count": len(fixed_income),
            "sovereign_pct": round(issuer.get(SOVEREIGN, 0.0), 4),
            "corporate_pct": round(issuer.get(CORPORATE, 0.0), 4),
            "cash_equivalent_pct": round(issuer.get(CASH_LIKE, 0.0), 4),
            "money_market_pct": round(money_market, 4),
            "avg_maturity_years": round(weighted_years / dated_weight, 4) if dated_weight else None,
            "maturity_coverage_pct": round(dated_weight / fi_weight * 100, 4) if fi_weight else None,
            "maturity_buckets": {label: round(value, 4) for label, value in buckets.items()},
        }

    return {
        "as_of": snapshot.as_of,
        "prev_as_of": snapshot.prev_as_of,
        "total": snapshot.total_holdings,
        "stocks": len(equity),
        "top10_pct": round(sum(row["holding_pct"] for row in non_cash[:10]), 4) if non_cash else None,
        "equity_pct": num(snapshot.equity_pct),
        "debt_pct": num(snapshot.debt_pct),
        "fund_unit_pct": num(snapshot.fund_unit_pct),
        "commodity_pct": num(snapshot.commodity_pct),
        "cash_pct": num(snapshot.cash_pct),
        "net_equity_pct": num(snapshot.net_equity_exposure_pct),
        "derivatives_net_pct": num(snapshot.derivatives_net_pct),
        "sectors": {name: round(value, 4) for name, value in sorted(sectors.items(), key=lambda item: -item[1])},
        "style": _rebased(styles, equity_weight, STYLE_BUCKETS + ("Other",)) if equity_weight else None,
        "cap": _rebased(caps, equity_weight, CAP_BUCKETS + (UNCLASSIFIED,)) if equity_weight else None,
        "changes": {
            "new": snapshot.new_additions_count,
            "exits": snapshot.complete_exits_count,
            "increased": snapshot.increased_count,
            "decreased": snapshot.decreased_count,
            "unchanged": snapshot.unchanged_count,
        },
        "fixed_income": fixed_income_summary,
    }


def unique_holdings(rows_by_scheme):
    """isin -> set(scheme_id) for live equity rows across the peer group."""
    owners = defaultdict(set)
    for scheme_id, rows in rows_by_scheme.items():
        for row in rows:
            if not row["is_exit"] and is_equity(row["holding_type"]) and row["isin"] and (row["holding_pct"] or 0) > 0:
                owners[row["isin"]].add(scheme_id)
    return owners


def stock_flows(rows_by_scheme, names):
    """Aggregate disclosure-to-disclosure changes across the peer group."""
    flows = {}
    sector_change = defaultdict(float)
    for scheme_id, rows in rows_by_scheme.items():
        for row in rows:
            if not is_equity(row["holding_type"]) or not row["isin"]:
                continue
            entry = flows.setdefault(row["isin"], {
                "isin": row["isin"],
                "name": row["name"],
                "industry": row["industry"],
                "holders": 0,
                "new": [],
                "exited": [],
                "increased": 0,
                "decreased": 0,
                "total_change_pct": 0.0,
            })
            status = row["status"]
            change = row["change_pct"]
            if row["is_exit"]:
                entry["exited"].append({"fund": names[scheme_id], "prev_pct": row["prev_holding_pct"]})
                change = -(row["prev_holding_pct"] or 0)
            else:
                entry["holders"] += 1
                if status == "NEW":
                    entry["new"].append({"fund": names[scheme_id], "pct": row["holding_pct"]})
                elif status == "INCREASED":
                    entry["increased"] += 1
                elif status == "DECREASED":
                    entry["decreased"] += 1
            if change is not None:
                entry["total_change_pct"] += change
                sector_change[row["industry"] or "Unspecified"] += change
    for entry in flows.values():
        entry["buyers"] = len(entry["new"]) + entry["increased"]
        entry["sellers"] = len(entry["exited"]) + entry["decreased"]
        entry["net_funds"] = entry["buyers"] - entry["sellers"]
        entry["total_change_pct"] = round(entry["total_change_pct"], 4)
    items = list(flows.values())
    top = 15
    return {
        "new_entries": sorted(
            [item for item in items if len(item["new"]) >= 2], key=lambda item: (-len(item["new"]), -item["total_change_pct"])
        )[:top],
        "full_exits": sorted(
            [item for item in items if len(item["exited"]) >= 2], key=lambda item: (-len(item["exited"]), item["total_change_pct"])
        )[:top],
        "net_buys": sorted(
            [item for item in items if item["net_funds"] > 0 and item["buyers"] >= 2],
            key=lambda item: (-item["net_funds"], -item["total_change_pct"]),
        )[:top],
        "net_sells": sorted(
            [item for item in items if item["net_funds"] < 0 and item["sellers"] >= 2],
            key=lambda item: (item["net_funds"], item["total_change_pct"]),
        )[:top],
        "sector_change": sorted(
            ({"sector": sector, "total_change_pct": round(value, 4)} for sector, value in sector_change.items()),
            key=lambda item: -item["total_change_pct"],
        ),
    }


def quant_scores(funds, horizon):
    """Percentile composite of the components in QUANT_SCORE_COMPONENTS."""
    def metric(fund, field):
        return (fund["metrics"].get(horizon) or {}).get(field)

    component_inputs = {}
    for key, _weight, source, higher in QUANT_SCORE_COMPONENTS:
        if source == "downside_protection":
            capture = percentile_ranks({f["code"]: metric(f, "downside_capture") for f in funds}, higher_is_better=False)
            drawdown = percentile_ranks({f["code"]: metric(f, "trailing_max_drawdown") for f in funds}, higher_is_better=True)
            combined = {}
            for fund in funds:
                parts = [value for value in (capture.get(fund["code"]), drawdown.get(fund["code"])) if value is not None]
                if parts:
                    combined[fund["code"]] = round(mean(parts), 4)
            component_inputs[key] = combined
        elif source == "ter":
            component_inputs[key] = percentile_ranks({f["code"]: f["ter"] for f in funds}, higher_is_better=higher)
        else:
            component_inputs[key] = percentile_ranks({f["code"]: metric(f, source) for f in funds}, higher_is_better=higher)

    scores = {}
    for fund in funds:
        components = {key: component_inputs[key].get(fund["code"]) for key, *_ in QUANT_SCORE_COMPONENTS}
        weighted = [(components[key], weight) for key, weight, *_ in QUANT_SCORE_COMPONENTS if components[key] is not None]
        total = None
        if len(weighted) >= QUANT_SCORE_MIN_COMPONENTS:
            total = round(sum(value * weight for value, weight in weighted) / sum(weight for _, weight in weighted), 4)
        scores[fund["code"]] = {"total": total, "components": components}
    ranked = sorted((code for code, score in scores.items() if score["total"] is not None), key=lambda code: -scores[code]["total"])
    for position, code in enumerate(ranked, start=1):
        scores[code]["rank"] = position
    return scores


def _average_nested(funds, path_getter, keys):
    return {key: avg(path_getter(fund, key) for fund in funds) for key in keys}


def category_average(funds, calendar_years):
    holdings = [fund["holdings"] for fund in funds if fund["holdings"]]
    average = {
        "aum": avg(fund["aum"] for fund in funds),
        "ter": avg(fund["ter"] for fund in funds),
        "age_months": avg(fund["age_months"] for fund in funds),
        "all_time_mdd": avg(fund["all_time_mdd"] for fund in funds),
        "returns": _average_nested(funds, lambda fund, key: fund["returns"].get(key), RETURN_KEYS),
        "calendar": {year: avg(fund["calendar"].get(year) for fund in funds) for year in calendar_years},
        "metrics": {
            horizon: {
                field: avg((fund["metrics"].get(horizon) or {}).get(field) for fund in funds)
                for field in {key for fund in funds for key in (fund["metrics"].get(horizon) or {})}
            }
            for horizon in HORIZONS
        },
        "calmar": {horizon: avg(fund["calmar"].get(horizon) for fund in funds) for horizon in HORIZONS},
        "score": {
            "total": avg(fund["score"]["total"] for fund in funds),
            "components": {
                key: avg(fund["score"]["components"].get(key) for fund in funds) for key, *_ in QUANT_SCORE_COMPONENTS
            },
        },
        "holdings": None,
    }
    if holdings:
        scalar_keys = (
            "total", "stocks", "top10_pct", "equity_pct", "debt_pct", "fund_unit_pct",
            "commodity_pct", "cash_pct", "net_equity_pct",
        )
        sector_totals = defaultdict(float)
        equity_holdings = [item for item in holdings if item["sectors"]]
        for item in equity_holdings:
            for sector, value in item["sectors"].items():
                sector_totals[sector] += value
        average["holdings"] = {
            **{key: avg(item[key] for item in holdings) for key in scalar_keys},
            "sectors": {
                sector: round(total / len(equity_holdings), 4)
                for sector, total in sorted(sector_totals.items(), key=lambda pair: -pair[1])
            } if equity_holdings else {},
            "style": {
                bucket: avg((item["style"] or {}).get(bucket) for item in holdings if item["style"])
                for bucket in STYLE_BUCKETS + ("Other",)
            },
            "cap": {
                bucket: avg((item["cap"] or {}).get(bucket) for item in holdings if item["cap"])
                for bucket in CAP_BUCKETS + (UNCLASSIFIED,)
            },
            "fixed_income": {
                key: avg((item["fixed_income"] or {}).get(key) for item in holdings if item["fixed_income"])
                for key in (
                    "weight_pct", "sovereign_pct", "corporate_pct", "cash_equivalent_pct",
                    "money_market_pct", "avg_maturity_years",
                )
            },
            "unique_count": avg(fund["unique"]["count"] for fund in funds if fund["holdings"]),
            "unique_weight_pct": avg(fund["unique"]["weight_pct"] for fund in funds if fund["holdings"]),
        }
    return average


# A sleeve (equity / fixed income) earns its own charts only when it is a
# real part of the category: at least this weight in at least this share of
# the funds. Stray rows (e.g. one gold ETF filing bullion as "debt") stay out.
MATERIAL_SLEEVE_PCT = 10
MATERIAL_SLEEVE_SHARE = 0.25


def _sleeve_is_material(funds, has_sleeve):
    with_holdings = [fund["holdings"] for fund in funds if fund["holdings"]]
    if not with_holdings:
        return False
    count = sum(1 for holdings in with_holdings if has_sleeve(holdings))
    return count >= max(1, MATERIAL_SLEEVE_SHARE * len(with_holdings))


def build_category_dashboard(category, plan, horizon, benchmark_slug=None):
    single_plan = is_single_plan_category(category)
    schemes = peer_schemes(category, plan)
    scheme_ids = [scheme.pk for scheme in schemes]
    metrics = _metrics_by_scheme(scheme_ids, full_horizon=horizon)
    snapshots, borrowed = snapshots_with_sibling_fallback(schemes)
    rows_by_snapshot = _holding_rows([snapshot.pk for snapshot in snapshots.values()])
    rows_by_scheme = {scheme_id: rows_by_snapshot.get(snapshot.pk, []) for scheme_id, snapshot in snapshots.items()}
    cap_lookup = cap_list.classification_lookup()
    cap_meta = cap_list.classification_meta()
    owners = unique_holdings(rows_by_scheme)

    names = {scheme.pk: short_fund_name(scheme.name) for scheme in schemes}
    funds = []
    for scheme in schemes:
        fund_metrics = metrics.get(scheme.pk, {})
        snapshot = snapshots.get(scheme.pk)
        rows = rows_by_scheme.get(scheme.pk, [])
        holdings = summarise_holdings(snapshot, rows, cap_lookup, cap_meta["method"])
        unique_rows = [
            row for row in rows
            if not row["is_exit"] and is_equity(row["holding_type"]) and row["isin"]
            and owners.get(row["isin"]) == {scheme.pk}
        ]
        unique_weight = sum(row["holding_pct"] or 0 for row in unique_rows)
        equity_pct = (holdings or {}).get("equity_pct")
        funds.append({
            "code": scheme.amfi_code,
            "name": scheme.name,
            "short_name": names[scheme.pk],
            "amc": scheme.amc,
            "plan": scheme.plan,
            "option": scheme.option,
            "isin": scheme.isin,
            "age_label": scheme.age_label,
            "age_months": num(scheme.age_months),
            "first_nav_date": scheme.first_nav_date,
            "aum": num(scheme.avg_aum_crore),
            "aum_period": scheme.aum_period,
            "ter": num(scheme.ter),
            "nav": num(scheme.latest_nav),
            "nav_date": scheme.latest_nav_date,
            "all_time_mdd": num(scheme.all_time_max_drawdown),
            "returns": _scheme_returns(scheme),
            "calendar": {year: num(value) for year, value in (scheme.calendar_returns or {}).items()},
            "metrics": fund_metrics,
            "calmar": {h: _calmar(fund_metrics.get(h) or {}) for h in HORIZONS},
            "holdings": holdings,
            "holdings_from_code": borrowed.get(scheme.pk),
            "unique": {
                "count": len(unique_rows),
                "weight_pct": round(unique_weight, 4),
                "share_of_equity_pct": round(unique_weight / equity_pct * 100, 4) if equity_pct else None,
            },
        })

    scores = quant_scores(funds, horizon)
    for fund in funds:
        fund["score"] = scores.get(fund["code"], {"total": None, "components": {}})

    years = sorted({year for fund in funds for year in fund["calendar"]}, reverse=True)[:CALENDAR_YEARS_SHOWN]
    calendar_years = sorted(years)
    benchmark = resolve_benchmark(category, benchmark_slug)
    medians = {
        median.horizon: median.values
        for median in MFCategoryMedian.objects.filter(category=category)
    }
    holdings_dates = Counter(fund["holdings"]["as_of"] for fund in funds if fund["holdings"])
    nav_dates = [fund["nav_date"] for fund in funds if fund["nav_date"]]
    last_run = MFSyncRun.objects.order_by("-started_at").first()

    return {
        "category": {
            "slug": category.slug,
            "name": category.name,
            "asset_class": category.asset_class,
            "peer_count_all_plans": category.peer_count,
            "metrics_as_of": category.metrics_as_of,
            "metrics_synced_at": category.metrics_synced_at,
            "total_schemes": category.total_schemes,
        },
        "plan": None if single_plan else plan,
        "plans": [] if single_plan else list(PLAN_CHOICES),
        "horizon": horizon,
        "horizons": list(HORIZONS),
        "as_of": {
            "nav": max(nav_dates) if nav_dates else None,
            "metrics": category.metrics_as_of,
            "holdings": holdings_dates.most_common(1)[0][0] if holdings_dates else None,
        },
        "benchmark": benchmark_payload(benchmark),
        "benchmarks": [
            {"slug": item.slug, "name": item.name, "asset_type": item.asset_type}
            for item in MFBenchmark.objects.exclude(latest_date__isnull=True)
        ],
        "calendar_years": calendar_years,
        "funds": funds,
        "category_average": category_average(funds, calendar_years),
        "category_median_all_plans": medians,
        "activity": stock_flows(rows_by_scheme, names),
        "cap_classification": cap_meta,
        "flags": {
            "has_holdings": any(fund["holdings"] for fund in funds),
            "has_equity": _sleeve_is_material(
                funds, lambda holdings: holdings.get("stocks") and (holdings.get("equity_pct") or 0) >= MATERIAL_SLEEVE_PCT
            ),
            "has_fixed_income": _sleeve_is_material(
                funds, lambda holdings: (holdings.get("fixed_income") or {}).get("weight_pct", 0) >= MATERIAL_SLEEVE_PCT
            ),
            "has_returns": any(fund["returns"].get("1y") is not None for fund in funds),
            "has_metrics": any(fund["metrics"] for fund in funds),
        },
        "sector_styles": {
            sector: sector_style(sector)
            for sector in sorted({name for fund in funds for name in ((fund["holdings"] or {}).get("sectors") or {})})
        },
        "score_methodology": [
            {"key": key, "weight": weight, "source": source, "higher_is_better": higher}
            for key, weight, source, higher in QUANT_SCORE_COMPONENTS
        ],
        "last_sync": sync_run_payload(last_run),
    }


def sync_run_payload(run):
    if run is None:
        return None
    return {
        "id": run.pk,
        "status": run.status,
        "trigger": run.trigger,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "current_step": run.current_step,
        "scope": run.scope,
        "stats": run.stats,
        "errors": run.errors[:20],
        "error_count": len(run.errors),
    }


def _rebase_series(points):
    """points: [(month, value)] -> values rebased to 100 at the first point."""
    if not points:
        return []
    base = points[0][1]
    return [round(float(value / base * 100), 4) for _, value in points]


def growth_series(scheme, benchmark):
    navs = list(MFNavMonthly.objects.filter(scheme=scheme).order_by("month").values_list("month", "nav"))
    if not navs:
        return None
    bench = {}
    if benchmark is not None:
        bench = dict(MFBenchmarkMonthly.objects.filter(benchmark=benchmark).values_list("month", "value"))
    # Keep only months both series cover so both start at 100 together.
    if bench:
        aligned = [(month, nav, bench[month]) for month, nav in navs if month in bench]
    else:
        aligned = [(month, nav, None) for month, nav in navs]
    aligned = aligned[-121:]
    if not aligned:
        return None
    return {
        "months": [month.isoformat()[:7] for month, *_ in aligned],
        "fund": _rebase_series([(month, nav) for month, nav, _ in aligned]),
        "benchmark": _rebase_series([(month, value) for month, _, value in aligned]) if bench else [],
        "benchmark_name": benchmark.name if benchmark is not None and bench else None,
    }


def holding_row_payload(row, snapshot_date, cap_lookup, cap_method):
    payload = {
        "isin": row["isin"],
        "name": row["name"],
        "industry": row["industry"],
        "type": row["holding_type"],
        "pct": row["holding_pct"],
        "prev_pct": row["prev_holding_pct"],
        "change_pct": row["change_pct"],
        "status": row["status"],
    }
    if is_equity(row["holding_type"]):
        payload["cap"] = cap_bucket(row["isin"], cap_lookup, cap_method)
        payload["style"] = sector_style(row["industry"])
    if is_fixed_income(row["holding_type"]):
        maturity = maturity_from_name(row["name"])
        payload["issuer_type"] = fixed_income_issuer_type(row["isin"], row["name"])
        payload["maturity"] = maturity
        payload["years_to_maturity"] = (
            round(max((maturity - snapshot_date).days / 365.25, 0.0), 4) if maturity else None
        )
    return payload


def build_scheme_detail(scheme, benchmark_slug=None):
    category = scheme.category
    metrics = _metrics_by_scheme([scheme.pk]).get(scheme.pk, {})
    medians = {}
    if category is not None:
        medians = {median.horizon: median.values for median in MFCategoryMedian.objects.filter(category=category)}
    benchmark = resolve_benchmark(category, benchmark_slug) if category else None
    cap_lookup = cap_list.classification_lookup()
    cap_meta = cap_list.classification_meta()

    holdings_scheme = scheme
    if not MFHoldingSnapshot.objects.filter(scheme=scheme).exists():
        _, borrowed = snapshots_with_sibling_fallback([scheme])
        if borrowed.get(scheme.pk):
            holdings_scheme = MFScheme.objects.get(amfi_code=borrowed[scheme.pk])
    snapshots = list(MFHoldingSnapshot.objects.filter(scheme=holdings_scheme).order_by("-as_of")[:24])
    rows_by_snapshot = _holding_rows([snapshot.pk for snapshot in snapshots])
    latest = snapshots[0] if snapshots else None
    latest_rows = rows_by_snapshot.get(latest.pk, []) if latest else []

    history = []
    for snapshot in reversed(snapshots):
        summary = summarise_holdings(snapshot, rows_by_snapshot.get(snapshot.pk, []), cap_lookup, cap_meta["method"])
        history.append({
            "as_of": snapshot.as_of,
            "total": summary["total"],
            "stocks": summary["stocks"],
            "top10_pct": summary["top10_pct"],
            "equity_pct": summary["equity_pct"],
            "debt_pct": summary["debt_pct"],
            "cash_pct": summary["cash_pct"],
            "cap": summary["cap"],
            "style": summary["style"],
        })

    unique = []
    peers_count = 0
    if category is not None and latest is not None:
        plan = scheme.plan if scheme.plan in PLAN_CHOICES else None
        peers = [peer for peer in peer_schemes(category, plan or PLAN_CHOICES[0]) if peer.pk != scheme.pk]
        peers_count = len(peers)
        peer_snapshots, _ = snapshots_with_sibling_fallback(peers)
        own_snapshot_ids = {snapshot.pk for snapshot in snapshots}
        peer_snapshots = {
            scheme_id: snapshot for scheme_id, snapshot in peer_snapshots.items() if snapshot.pk not in own_snapshot_ids
        }
        peer_isins = set(
            MFHolding.objects.filter(
                snapshot_id__in=[snapshot.pk for snapshot in peer_snapshots.values()],
                holding_type="equity",
                is_exit=False,
                holding_pct__gt=0,
            ).exclude(isin="").values_list("isin", flat=True)
        )
        unique = [
            holding_row_payload(row, latest.as_of, cap_lookup, cap_meta["method"])
            for row in latest_rows
            if not row["is_exit"] and is_equity(row["holding_type"]) and row["isin"]
            and (row["holding_pct"] or 0) > 0 and row["isin"] not in peer_isins
        ]
        unique.sort(key=lambda item: -(item["pct"] or 0))

    live_rows = sorted(
        (row for row in latest_rows if not row["is_exit"]),
        key=lambda row: -(row["holding_pct"] or 0),
    )
    exits = sorted(
        (row for row in latest_rows if row["is_exit"]),
        key=lambda row: -(row["prev_holding_pct"] or 0),
    )
    return {
        "scheme": {
            "code": scheme.amfi_code,
            "name": scheme.name,
            "short_name": short_fund_name(scheme.name),
            "amc": scheme.amc,
            "plan": scheme.plan,
            "option": scheme.option,
            "isin": scheme.isin,
            "category": {"slug": category.slug, "name": category.name, "asset_class": category.asset_class} if category else None,
            "age_label": scheme.age_label,
            "first_nav_date": scheme.first_nav_date,
            "aum": num(scheme.avg_aum_crore),
            "aum_period": scheme.aum_period,
            "ter": num(scheme.ter),
            "ter_date": scheme.ter_date,
            "nav": num(scheme.latest_nav),
            "nav_date": scheme.latest_nav_date,
            "all_time_mdd": num(scheme.all_time_max_drawdown),
            "returns": _scheme_returns(scheme),
            "calendar": {year: num(value) for year, value in (scheme.calendar_returns or {}).items()},
        },
        "metrics": metrics,
        "category_median_all_plans": medians,
        "benchmark": benchmark_payload(benchmark),
        "growth": growth_series(scheme, benchmark),
        "aum_history": [
            {"date": item.as_of_date, "period": item.period, "aum": num(item.avg_aum_crore)}
            for item in MFAumHistory.objects.filter(scheme=scheme).order_by("as_of_date")
        ],
        "holdings": {
            "as_of": latest.as_of if latest else None,
            "prev_as_of": latest.prev_as_of if latest else None,
            "summary": summarise_holdings(latest, latest_rows, cap_lookup, cap_meta["method"]) if latest else None,
            "rows": [holding_row_payload(row, latest.as_of, cap_lookup, cap_meta["method"]) for row in live_rows] if latest else [],
            "exits": [holding_row_payload(row, latest.as_of, cap_lookup, cap_meta["method"]) for row in exits] if latest else [],
        },
        "holding_history": history,
        "unique_stocks": unique,
        "peers_count": peers_count,
        "holdings_from_code": holdings_scheme.amfi_code if holdings_scheme.pk != scheme.pk else None,
        "cap_classification": cap_meta,
    }


def categories_payload():
    categories = MFCategory.objects.annotate(
        growth_regular=Count("schemes", filter=Q(schemes__is_active=True, schemes__plan="Regular", schemes__option="Growth")),
        growth_direct=Count("schemes", filter=Q(schemes__is_active=True, schemes__plan="Direct", schemes__option="Growth")),
        single_plan=Count("schemes", filter=Q(schemes__is_active=True, schemes__plan="Other")),
        nav_synced=Count("schemes", filter=Q(schemes__nav_synced_at__isnull=False)),
        last_nav_sync=Max("schemes__nav_synced_at"),
        last_holdings_sync=Max("schemes__holdings_synced_at"),
    ).order_by("display_order", "name")
    groups = defaultdict(list)
    for category in categories:
        groups[category.asset_class or "Other"].append({
            "slug": category.slug,
            "name": category.name,
            "asset_class": category.asset_class,
            "total_schemes": category.total_schemes,
            "funds": {
                "Regular": category.growth_regular,
                "Direct": category.growth_direct,
                "Other": category.single_plan,
            },
            "single_plan": is_single_plan_category(category),
            "metrics_synced_at": category.metrics_synced_at,
            "nav_synced_schemes": category.nav_synced,
            "last_nav_sync": category.last_nav_sync,
            "last_holdings_sync": category.last_holdings_sync,
            "default_benchmark": default_benchmark_slug(category),
            "has_data": bool(category.metrics_synced_at),
        })
    order = ["Equity", "Hybrid", "Debt", "Solution Oriented", "ETF", "Other"]
    ordered = sorted(groups.items(), key=lambda item: order.index(item[0]) if item[0] in order else len(order))
    last_run = MFSyncRun.objects.order_by("-started_at").first()
    return {
        "groups": [{"asset_class": name, "categories": items} for name, items in ordered],
        "last_sync": sync_run_payload(last_run),
        "today": timezone.localdate(),
    }
