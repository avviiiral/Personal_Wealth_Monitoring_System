"""Deterministic stand-ins for the CharlieAlpha API used by the tests.

Payload shapes mirror real responses captured from api.charliealpha.in
(trimmed to the fields PWMS reads).
"""

from datetime import date, timedelta

from mf_intelligence.constants import HORIZONS, METRIC_FIELDS
from mf_intelligence.services.client import CharlieAlphaNotFound


def business_days(start, end):
    current = start
    while current <= end:
        if current.weekday() < 5:
            yield current
        current += timedelta(days=1)


def nav_series(start, end, start_nav, daily_growth):
    dates, navs = [], []
    nav = start_nav
    for day in business_days(start, end):
        dates.append(day.isoformat())
        navs.append(round(nav, 4))
        nav *= 1 + daily_growth
    return dates, navs


def metric_block(cagr, sortino, p25, downside, drawdown):
    block = {field: None for field in METRIC_FIELDS}
    block.update(
        trailing_cagr=cagr,
        sortino_ratio=sortino,
        sharpe_ratio=round(sortino * 0.7, 2),
        rolling_return_p25=p25,
        rolling_return_p50=p25 + 3,
        downside_capture=downside,
        upside_capture=100.0,
        trailing_max_drawdown=drawdown,
        amfi_volatility=15.0,
        beta=0.95,
        alpha=1.0,
    )
    return block


FUND_A = "100001"  # Alpha Multi Cap Fund - Regular Growth
FUND_A_DIRECT = "100002"
FUND_B = "100003"  # Beta Multi Cap Fund - Regular Growth
FUND_A_IDCW = "100004"
FUND_UNCLAIMED = "100005"
LIQUID_FUND = "200001"


class FakeCharlieAlpha:
    """Records every call; returns canned payloads."""

    def __init__(self, as_of=date(2026, 11, 23)):
        self.as_of = as_of
        self.calls = []
        self.request_count = 0
        self.scheme_master = [
            {
                "category": "Equity Scheme - Multi Cap Fund",
                "category_slug": "multi-cap",
                "asset_class": "Equity",
                "total_schemes": 5,
                "schemes": [
                    {"code": FUND_A, "name": "Alpha Multi Cap Fund - Regular Plan - Growth", "amc": "Alpha MF", "plan": "Regular", "option": "Growth"},
                    {"code": FUND_A_DIRECT, "name": "Alpha Multi Cap Fund - Direct Plan - Growth", "amc": "Alpha MF", "plan": "Direct", "option": "Growth"},
                    {"code": FUND_B, "name": "Beta Multicap Fund - Regular Growth", "amc": "Beta MF", "plan": "Regular", "option": "Growth"},
                    {"code": FUND_A_IDCW, "name": "Alpha Multi Cap Fund - Regular Plan - IDCW", "amc": "Alpha MF", "plan": "Regular", "option": "IDCW"},
                    {"code": FUND_UNCLAIMED, "name": "Alpha Multi Cap Fund - Unclaimed Redemption - Growth", "amc": "Alpha MF", "plan": "Regular", "option": "Growth"},
                ],
            },
            {
                "category": "Debt Scheme - Liquid Fund",
                "category_slug": "liquid",
                "asset_class": "Debt",
                "total_schemes": 1,
                "schemes": [
                    {"code": LIQUID_FUND, "name": "Gamma Liquid Fund - Regular Plan - Growth", "amc": "Gamma MF", "plan": "Regular", "option": "Growth"},
                ],
            },
        ]
        self.missing_holdings = set()
        self.fail_codes = set()

    def _record(self, name, *args):
        self.calls.append((name, *args))
        self.request_count += 1

    def calls_named(self, name):
        return [call for call in self.calls if call[0] == name]

    def categories(self):
        self._record("categories")
        return {
            "total": 2,
            "categories": {
                "Equity": [{"name": "Equity Scheme - Multi Cap Fund", "slug": "multi-cap"}],
                "Debt": [{"name": "Debt Scheme - Liquid Fund", "slug": "liquid"}],
            },
        }

    def schemes(self, category_slug=None):
        self._record("schemes")
        return {"total_categories": len(self.scheme_master), "categories": self.scheme_master}

    def metrics_by_category(self, slug):
        self._record("metrics", slug)
        if slug == "multi-cap":
            rows = [
                (FUND_A, "Alpha Multi Cap Fund - Regular Plan - Growth", "Regular", 1500.0, 1.80, (18.0, 1.2, 12.0, 85.0, -15.0)),
                (FUND_A_DIRECT, "Alpha Multi Cap Fund - Direct Plan - Growth", "Direct", 400.0, 0.60, (19.0, 1.3, 13.0, 84.0, -15.0)),
                (FUND_B, "Beta Multicap Fund - Regular Growth", "Regular", 900.0, 2.20, (12.0, 0.6, 6.0, 105.0, -25.0)),
            ]
        else:
            rows = [(LIQUID_FUND, "Gamma Liquid Fund - Regular Plan - Growth", "Regular", 5000.0, 0.30, (6.5, 2.0, 6.0, 10.0, -0.1))]
        schemes = []
        for code, name, plan, aum, ter, values in rows:
            schemes.append({
                "code": code,
                "name": name,
                "slug": name.lower().replace(" ", "-"),
                "plan": plan,
                "option": "Growth",
                "amc_name": name.split(" ")[0] + " MF",
                "data_as_of": self.as_of.isoformat(),
                "avg_aum_crore": aum,
                "aum_period": "April–June 2026",
                "ter": ter,
                "ter_date": "2026-11-20",
                "age": "6Y, 1M",
                "age_months": 73.0,
                "all_time_max_drawdown": values[4] - 5,
                "metrics": {horizon: metric_block(*values) for horizon in HORIZONS},
            })
        median = {field: 1.0 for field in METRIC_FIELDS}
        return {
            "category": "x",
            "category_slug": slug,
            "total_schemes": len(schemes),
            "schemes": schemes,
            "category_medians": {
                "peer_count": len(schemes),
                "all_time_max_drawdown": -20.0,
                "metrics": {horizon: median for horizon in HORIZONS},
                "sample_counts": {"metrics": {horizon: {field: len(schemes) for field in METRIC_FIELDS} for horizon in HORIZONS}},
            },
        }

    def nav_history(self, code):
        self._record("nav", code)
        if code in self.fail_codes:
            raise RuntimeError("boom")
        growth = {FUND_A: 0.0006, FUND_A_DIRECT: 0.00065, FUND_B: 0.0003, LIQUID_FUND: 0.00025}.get(code, 0.0004)
        dates, navs = nav_series(date(2019, 1, 1), self.as_of, 10.0, growth)
        return {"scheme_code": code, "ter": 1.5, "ter_date": "2026-11-20", "avg_aum_crore": 1234.5,
                "aum_period": "April–June 2026", "dates": dates, "nav": navs}

    def holdings(self, code, as_of=None, change=True):
        self._record("holdings", code)
        if code in self.missing_holdings:
            raise CharlieAlphaNotFound("404")
        if code == LIQUID_FUND:
            holdings = [
                {"isin": None, "name": "Cash", "industry": "", "holding_type": "cash", "holding_pct": 10.0, "status": "INCREASED"},
                {"isin": "IN002026X248", "name": "91 Days Tbill (MD 17/12/2026)", "industry": "", "holding_type": "money_market", "holding_pct": 40.0, "status": "NEW"},
                {"isin": "INE403D08306", "name": "7.30% Bharti Telecom Ltd. (01/12/2027)", "industry": "", "holding_type": "debt", "holding_pct": 50.0, "status": "UNCHANGED"},
            ]
            allocation = {"equity_pct": 0.0, "debt_pct": 90.0, "cash_pct": 10.0}
            exits = []
        else:
            shared = [
                {"isin": "INE040A01034", "name": "HDFC Bank Ltd.", "industry": "Banks", "holding_type": "equity", "holding_pct": 9.0,
                 "prev_holding_pct": 8.0, "change_pct": 1.0, "status": "INCREASED", "quantity": 100.0},
                {"isin": "INE009A01021", "name": "Infosys Ltd.", "industry": "IT - Software", "holding_type": "equity", "holding_pct": 6.0,
                 "prev_holding_pct": None, "change_pct": 6.0, "status": "NEW"},
            ]
            own = {
                FUND_A: [{"isin": "INE111A01011", "name": "Alpha Only Ltd.", "industry": "Power", "holding_type": "equity", "holding_pct": 4.0,
                          "prev_holding_pct": 4.0, "change_pct": 0.0, "status": "UNCHANGED"}],
                FUND_B: [{"isin": "INE222B01022", "name": "Beta Only Ltd.", "industry": "Banks", "holding_type": "equity", "holding_pct": 3.0,
                          "prev_holding_pct": 3.5, "change_pct": -0.5, "status": "DECREASED"}],
            }.get(code, [])
            holdings = [{"isin": None, "name": "Cash", "industry": "", "holding_type": "cash", "holding_pct": 5.0}] + shared + own
            allocation = {"equity_pct": 95.0, "debt_pct": 0.0, "cash_pct": 5.0}
            exits = [{"isin": "INE333C01033", "name": "Exited Ltd.", "industry": "Realty", "holding_type": "equity",
                      "prev_holding_pct": 2.0, "status": "EXITED"}]
        return {
            "amfi_code": code,
            "isin": f"INF{code}XX01",
            "as_of": "2026-10-31",
            "prev_as_of": "2026-09-30",
            "comparison_summary": {"new_additions_count": 1, "complete_exits_count": len(exits), "increased_count": 1,
                                   "decreased_count": 0, "unchanged_count": 1},
            "total_holdings": len(holdings),
            "asset_allocation": {"physical": allocation, "derivatives": {"gross_exposure_pct": 0.0, "net_exposure_pct": 0.0, "count": 0},
                                 "net_equity_exposure_pct": allocation["equity_pct"]},
            "holdings": holdings,
            "exits": exits,
        }

    def aum_history(self, code):
        self._record("aum", code)
        return {"scheme_code": code, "history": [
            {"as_of_date": "2026-03-31", "period": "January–March 2026", "avg_aum_crore": 1000.0},
            {"as_of_date": "2026-06-30", "period": "April–June 2026", "avg_aum_crore": 1100.0},
        ]}

    def benchmarks(self):
        self._record("benchmarks")
        return {"total": 2, "benchmarks": [
            {"name": "NIFTY 500 TRI", "slug": "nifty-500-tri", "type": "Equity"},
            {"name": "NIFTY500 MULTICAP 50:25:25 TRI", "slug": "nifty500-multicap-50-25-25-tri", "type": "Equity"},
        ]}

    def benchmark_history(self, slug):
        self._record("benchmark", slug)
        dates, values = nav_series(date(2015, 1, 1), self.as_of, 100.0, 0.0005)
        return {"slug": slug, "dates": dates, "nav": values}
