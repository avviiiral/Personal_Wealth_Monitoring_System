# Mutual Fund Intelligence

Category-level analytics for every SEBI mutual fund category, modelled on the
Nexedge "Multi Cap Intelligence" report but generated for all 64 categories
from data stored in the PWMS database.

Page: **Portfolio → MF Intelligence** (`/mf-intelligence`).
Backend app: `backend/mf_intelligence`. API prefix: `/api/mf-intelligence/`.

## Data sources

| Data | Source | Refresh rule |
| --- | --- | --- |
| Category catalogue and scheme master | CharlieAlpha `GET /v1/mf/categories`, `GET /v1/mf/schemes` | every sync |
| Risk / return metrics for 1Y, 3Y, 5Y, 10Y (CAGR, rolling-return distribution, SIP XIRR, volatility, beta, alpha, Sharpe, Sortino, information ratio, capture ratios, drawdown), category medians, TER, quarterly average AUM | `GET /v1/mf/metrics-by-cat/{slug}` (one call per category) | every sync |
| NAV history → 1W…10Y, YTD, since-inception and calendar-year returns, month-end NAV | `GET /v1/mf/hnav/{code}` | once per day per scheme |
| Portfolio disclosure, asset allocation, changes vs previous disclosure | `GET /v1/mf/holdings/{code}?change=yes` | when older than 7 days |
| Quarterly average AUM history | `GET /v1/mf/aum/{code}` | when older than 30 days |
| Benchmarks (22 indices) | `GET /v1/benchmark/list`, `GET /v1/benchmark/hnav/{slug}` | once per day |
| Large / Mid / Small cap list | AMFI `AverageMarketCapitalization<DD><Mon><YYYY>.xlsx` | when a newer half-year is published |

CharlieAlpha is public, unauthenticated and limited to 50 requests per 10
seconds. The client stays at 40 per 10 seconds (configurable) and retries
429 / 5xx responses.

If the AMFI workbook cannot be downloaded, stocks held by NIFTY 100 trackers are
treated as Large Cap and NIFTY Midcap 150 trackers as Mid Cap (other Indian
equities as Small Cap). The page says when this proxy is in use.

## What is stored

All tables are market-wide reference data (no owner or family columns):
`MFCategory`, `MFScheme` (scheme facts + computed returns), `MFSchemeMetric`
(30 metrics × 4 horizons), `MFCategoryMedian`, `MFNavMonthly`,
`MFHoldingSnapshot` + `MFHolding` (every disclosure kept, so trends build up),
`MFAumHistory`, `MFBenchmark` + `MFBenchmarkMonthly`, `MFStockClassification`
and `MFSyncRun` (audit of every sync). Daily NAV is fetched on each sync but only
month-end values are stored; computed values use four decimal places and the
page shows two.

## Running a sync

```bash
cd backend
python manage.py migrate
python manage.py sync_mf_intelligence --category multi-cap      # one category, about 1 minute
python manage.py sync_mf_intelligence                           # all categories, Regular plans, ~25-30 minutes
python manage.py sync_mf_intelligence --plan Both               # Regular and Direct NAV
python manage.py sync_mf_intelligence --force                   # ignore freshness rules
python manage.py import_mf_cap_classification --file AverageMarketCapitalization30Jun2026.xlsx
```

Admins (and above) can also start a sync from the page; Viewers can only read.
Holdings are a fund-level disclosure, so a Direct plan without its own
disclosure reuses its Regular sibling's holdings.

## Shared application consumers

The synced tables are shared market reference data and are reused by existing
PWMS features where the matching identity and data granularity are suitable:

- The Watch List joins each mutual-fund product to `MFScheme` by exact AMFI
  scheme code. It uses stored 1W–5Y and since-inception returns when its local
  performance snapshot is missing a value, and exposes the synced category,
  TER, AUM, NAV, calendar returns and risk metrics in the scheme payload.
- Family mutual-fund valuation can use a recent synced latest NAV when the
  family NAV / AMFI master has no usable value. Matching is by exact AMFI code
  (or exact ISIN for the shared portfolio NAV service), only NAVs dated no more
  than seven days old are eligible, and a NAV dated after the transaction date
  is never used for transaction/SIP execution.
- Mutual-fund look-through can use the latest `MFHoldingSnapshot` when it is
  newer than the family's stored disclosure. On the same disclosure date, the
  local disclosure is preferred because it may have additional source fields.
- Existing daily `AMFIMasterNAV` history remains the source for historical
  reports and date-specific calculations. `MFNavMonthly` is month-end data and
  is deliberately not substituted for daily NAV observations.

The integration is read-through / fallback only: it does not rewrite family
transactions, portfolio ownership, or user-entered holdings.

An optional daily sync is off by default. Enable it in
`backend/config/pwms_config.json` (or `PWMS_CONFIG_OVERRIDES`):

```json
"mf_intelligence": { "auto_sync_enabled": true, "auto_sync_time": "05:30", "auto_sync_plans": ["Regular"] }
```

## Calculation notes

- Returns: up to 6M and YTD are absolute; 1Y and longer cover N × 365 days and are
  annualised (CAGR), matching CharlieAlpha's trailing CAGR for the same NAV date.
- Calendar years appear only when the fund has a value at both year ends.
- Category average = simple mean of the funds shown (one plan); the API's
  category median covers all plans and is shown alongside where useful.
- Quant Score (computed in PWMS, not an API field): weighted mean of
  percentile ranks among the funds shown for the selected horizon — returns 25%,
  Sortino 25%, rolling-return P25 20%, downside protection (downside capture and
  max drawdown) 20%, TER 10%; at least three components are required.
- Calmar = horizon CAGR ÷ |horizon max drawdown|.
- Sector style (Cyclical / Defensive / Sensitive) follows the AMFI industry map in
  `mf_intelligence/constants.py`. Fixed-income maturity is read from dates printed
  in disclosure names; issuer type from the ISIN.
- Manager activity compares each fund's last two disclosures; weight changes
  include price drift.

The reference report's EDGE score, P/E, P/B, turnover, exit load, fund manager
and price-return attribution have no source in the CharlieAlpha API, so they are
not shown rather than estimated.
