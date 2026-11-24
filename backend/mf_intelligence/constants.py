"""Static taxonomy used by Mutual Fund Intelligence.

Nothing in this module is market data. It only describes how values that
come from the CharlieAlpha API are labelled and grouped (metric names,
horizons, sector style buckets, a default benchmark per SEBI category).
"""

HORIZONS = ("1Y", "3Y", "5Y", "10Y")
DEFAULT_HORIZON = "3Y"

# Field names returned by /mf/metrics-by-cat for every horizon. The model
# MFSchemeMetric stores one column per name, in this order.
METRIC_FIELDS = (
    "trailing_cagr",
    "avg_rolling_return",
    "max_rolling_return",
    "min_rolling_return",
    "rolling_return_p10",
    "rolling_return_p25",
    "rolling_return_p50",
    "rolling_return_p75",
    "rolling_return_p90",
    "rolling_return_sd",
    "trailing_xirr",
    "avg_rolling_xirr",
    "max_rolling_xirr",
    "min_rolling_xirr",
    "rolling_xirr_p10",
    "rolling_xirr_p25",
    "rolling_xirr_p50",
    "rolling_xirr_p75",
    "rolling_xirr_p90",
    "rolling_xirr_sd",
    "amfi_volatility",
    "beta",
    "correlation",
    "alpha",
    "upside_capture",
    "downside_capture",
    "sharpe_ratio",
    "sortino_ratio",
    "information_ratio",
    "trailing_max_drawdown",
)

# Compact metric subset sent for horizons other than the one selected.
SUMMARY_METRIC_FIELDS = (
    "trailing_cagr",
    "amfi_volatility",
    "beta",
    "alpha",
    "sharpe_ratio",
    "sortino_ratio",
    "information_ratio",
    "upside_capture",
    "downside_capture",
    "trailing_max_drawdown",
    "rolling_return_p50",
)

# Point-to-point return periods computed from NAV history.
# (field suffix, months, years, label). Periods under one year are
# absolute returns; one year and longer are annualised (CAGR).
RETURN_PERIODS = (
    ("1w", None, None, "1W"),
    ("1m", 1, None, "1M"),
    ("3m", 3, None, "3M"),
    ("6m", 6, None, "6M"),
    ("1y", None, 1, "1Y"),
    ("2y", None, 2, "2Y"),
    ("3y", None, 3, "3Y"),
    ("5y", None, 5, "5Y"),
    ("10y", None, 10, "10Y"),
)

CALENDAR_YEARS_KEPT = 10

PLAN_REGULAR = "Regular"
PLAN_DIRECT = "Direct"
PLAN_OTHER = "Other"
PLAN_CHOICES = (PLAN_REGULAR, PLAN_DIRECT)

# Options whose NAV is a total-return series. IDCW / Bonus NAVs drop on
# payout dates, so returns computed from them would be misleading.
GROWTH_OPTIONS = ("Growth",)
# Exchange traded funds and single-plan schemes carry plan/option "Other".
SINGLE_PLAN_OPTIONS = ("Growth", "Other")

# Cyclical / Defensive / Sensitive style buckets keyed by the AMFI
# industry names used in portfolio disclosures.
SECTOR_STYLE = {
    "Banks": "Cyclical",
    "Finance": "Cyclical",
    "Financial Technology (Fintech)": "Cyclical",
    "Capital Markets": "Cyclical",
    "Automobiles": "Cyclical",
    "Auto Components": "Cyclical",
    "Agricultural, Commercial & Construction Vehicles": "Cyclical",
    "Ferrous Metals": "Cyclical",
    "Diversified Metals": "Cyclical",
    "Non - Ferrous Metals": "Cyclical",
    "Metals & Minerals Trading": "Cyclical",
    "Minerals & Mining": "Cyclical",
    "Cement & Cement Products": "Cyclical",
    "Realty": "Cyclical",
    "Chemicals & Petrochemicals": "Cyclical",
    "Fertilizers & Agrochemicals": "Cyclical",
    "Consumer Durables": "Cyclical",
    "Textiles & Apparels": "Cyclical",
    "Diversified": "Cyclical",
    "Construction": "Cyclical",
    "Diamond  &  Jewellery": "Cyclical",
    "Plastic Products": "Cyclical",
    "Retailing": "Cyclical",
    "Pharmaceuticals & Biotechnology": "Defensive",
    "Healthcare Services": "Defensive",
    "Healthcare Equipment & Supplies": "Defensive",
    "IT - Software": "Defensive",
    "IT - Hardware": "Defensive",
    "IT - Services": "Defensive",
    "Diversified FMCG": "Defensive",
    "Household Products": "Defensive",
    "Personal Products": "Defensive",
    "Food Products": "Defensive",
    "Cigarettes & Tobacco Products": "Defensive",
    "Beverages": "Defensive",
    "Insurance": "Defensive",
    "Agricultural Food & other Products": "Sensitive",
    "Telecom - Services": "Sensitive",
    "Telecom -  Equipment & Accessories": "Sensitive",
    "Industrial Products": "Sensitive",
    "Industrial Manufacturing": "Sensitive",
    "Engineering Services": "Sensitive",
    "Electrical Equipment": "Sensitive",
    "Aerospace & Defense": "Sensitive",
    "Oil": "Sensitive",
    "Petroleum Products": "Sensitive",
    "Consumable Fuels": "Sensitive",
    "Gas": "Sensitive",
    "Power": "Sensitive",
    "Other Utilities": "Sensitive",
    "Transport Infrastructure": "Sensitive",
    "Transport Services": "Sensitive",
    "Commercial Services & Supplies": "Sensitive",
    "Other Consumer Services": "Sensitive",
    "Leisure Services": "Sensitive",
    "Entertainment": "Sensitive",
    "Media": "Sensitive",
    "Printing & Publication": "Sensitive",
    "Paper, Forest & Jute Products": "Sensitive",
}
STYLE_BUCKETS = ("Cyclical", "Defensive", "Sensitive")
CAP_BUCKETS = ("Large Cap", "Mid Cap", "Small Cap")

# Default comparison index per SEBI category slug. The user can pick any
# other synced benchmark on the page; this only decides the first view.
DEFAULT_BENCHMARK_BY_CATEGORY = {
    "large-cap": "nifty-100-tri",
    "large-mid-cap": "nifty-largemidcap-250-tri",
    "mid-cap": "nifty-midcap-150-tri",
    "small-cap": "nifty-smallcap-250-tri",
    "multi-cap": "nifty500-multicap-50-25-25-tri",
    "overnight": "nifty-1d-rate-index",
    "liquid": "nifty-1d-rate-index",
    "gilt": "nifty-composite-g-sec-index",
    "10-year-gilt": "nifty-10-yr-benchmark-g-sec",
    "long-duration": "nifty-15-yr-and-above-g-sec-index",
    "etf-gold": "domestic-gold-995",
    "fof-gold": "domestic-gold-995",
    "etf-silver": "domestic-silver-999",
    "fof-silver": "domestic-silver-999",
    "arbitrage": "nifty-50-arbitrage",
}
DEFAULT_BENCHMARK_BY_ASSET_CLASS = {
    "Equity": "nifty-500-tri",
    "Hybrid": "nifty-50-tri",
    "ETF": "nifty-50-tri",
    "Solution Oriented": "nifty-500-tri",
    "Other": "nifty-500-tri",
    "Debt": "nifty-composite-g-sec-index",
}
FALLBACK_BENCHMARK = "nifty-500-tri"

# Quant score components: (key, weight, metric source, higher_is_better).
# Each component is a percentile rank inside the displayed peer group;
# the score is the weight-normalised mean of the components available.
QUANT_SCORE_COMPONENTS = (
    ("returns", 25, "trailing_cagr", True),
    ("risk_adjusted", 25, "sortino_ratio", True),
    ("consistency", 20, "rolling_return_p25", True),
    ("downside", 20, "downside_protection", True),
    ("cost", 10, "ter", False),
)
QUANT_SCORE_MIN_COMPONENTS = 3
