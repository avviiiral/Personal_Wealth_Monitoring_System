# Mutual Fund Intelligence

Category-level analytics for the SEBI mutual-fund categories returned by the
CharlieAlpha category catalogue, modelled on the Nexedge "Multi Cap Intelligence"
report and served from reference data persisted in the PWMS database. The number
and names of categories are refreshed from the provider rather than hard-coded.

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
`MFHoldingSnapshot` + `MFHolding` (disclosures are retained as dated snapshots),
`MFAumHistory`, `MFBenchmark` + `MFBenchmarkMonthly`, `MFStockClassification`
and `MFSyncRun` (audit of each sync run, including scope, progress, statistics
and captured errors). Daily NAV history is fetched from the provider, but the
persistent MF Intelligence NAV series is stored as month-end observations;
computed percentage/ratio values use four decimal places and the page rounds
them for display.

## Running a sync

```bash
cd backend
python manage.py migrate
python manage.py sync_mf_intelligence --category multi-cap --limit 20
python manage.py sync_mf_intelligence                           # all provider categories, Regular plan targets
python manage.py sync_mf_intelligence --plan Both               # Regular and Direct plan targets
python manage.py sync_mf_intelligence --force                   # bypass per-fund freshness checks
python manage.py sync_mf_intelligence --skip-nav --skip-holdings
python manage.py import_mf_cap_classification                   # download latest available AMFI workbook
python manage.py import_mf_cap_classification --file AverageMarketCapitalization30Jun2026.xlsx
```

Admins (and other roles allowed by `CanWriteFinancialData`) can start a sync
from the page; read-only users can inspect the stored analytics and sync status.
The sync API starts work in a background thread and returns HTTP 202; the page
polls the sync-status endpoint. The management command runs synchronously in the
calling process. A Direct plan without its own disclosure can reuse its Regular
sibling's holdings because the portfolio disclosure is fund-level.

`sync_mf_intelligence` accepts repeatable `--category` filters, `--plan
Regular|Direct|Both`, `--force`, `--limit`, `--workers` and independent
`--skip-nav`, `--skip-holdings`, `--skip-aum`, `--skip-benchmarks` and
`--skip-cap-list` switches. Category catalogue, scheme master and category
metrics are still refreshed during a run; the skip switches control the named
fund/benchmark/classification stages. `--limit` is intended for bounded test
runs. See the [complete PWMS command reference](../commands.md).

## Shared application consumers

The synced tables are shared market reference data and are reused by existing
PWMS features where the matching identity and data granularity are suitable:

- The Watch List joins each mutual-fund product to `MFScheme` by exact AMFI
  scheme code. It uses stored 1W–5Y and since-inception returns when its local
  performance snapshot is missing a value, and exposes the synced category,
  TER, AUM, NAV, calendar returns and risk metrics in the scheme payload.
- Family mutual-fund valuation compares the family NAV, the global AMFI master
  and a recent synced latest NAV, selecting the newest eligible observation.
  AMFI master wins ties; MF Intelligence can replace an older family copy.
  Matching is by exact AMFI code (or exact ISIN for the shared portfolio NAV
  service), only intelligence NAVs dated no more than seven days old are
  eligible, and a NAV dated after the transaction date is never used for
  transaction/SIP execution. The Mutual Funds summary and holdings APIs also
  calculate their returned market values from that same read-through NAV, so
  these screens do not wait for a family-holding rebuild to reflect the sync.
- Mutual-fund look-through can use the latest `MFHoldingSnapshot` when it is
  newer than the family's stored disclosure. On the same disclosure date, the
  local disclosure is preferred because it may have additional source fields.
- Existing daily `AMFIMasterNAV` history remains the source for historical
  reports and date-specific calculations. `MFNavMonthly` is month-end data and
  is deliberately not substituted for daily NAV observations.

The integration is read-through / fallback only: it does not rewrite family
transactions, portfolio ownership, or user-entered holdings.

The optional `MFIntelligenceScheduler` is separate from the `run_scheduled_refresh`
pipeline and is off by default. When enabled, it runs once per day using the
configured market timezone (default `Asia/Kolkata`). Configure it in
`backend/config/pwms_config.json` (or `PWMS_CONFIG_OVERRIDES`):

```json
"mf_intelligence": {
  "auto_sync_enabled": true,
  "auto_sync_time": "05:30",
  "auto_sync_plans": ["Regular"],
  "auto_sync_categories": null
}
```

## API surface

The Django URL configuration mounts the feature at `/api/mf-intelligence/`:

| Method | Endpoint | Purpose |
| --- | --- | --- |
| GET | `categories/` | Category catalogue and stored sync metadata |
| GET | `categories/<slug>/dashboard/` | Category dashboard; supports `plan`, `horizon` and optional `benchmark` query parameters |
| GET | `schemes/<code>/` | Scheme detail by exact AMFI scheme code; optional benchmark selection |
| POST | `sync/` | Starts a sync in a background thread; requires authentication and `CanWriteFinancialData` |
| GET | `sync/status/` | Reports whether a sync is running and returns the latest run record |

Category, dashboard, scheme and sync-status reads require authentication. The sync
mutation is separately protected by `CanWriteFinancialData`; frontend visibility
of the sync controls is not the security boundary.

## Persistence and concurrency

`MFIntelligenceSync` performs external HTTP reads through `CharlieAlphaClient`.
The client uses per-worker HTTP sessions, a shared sliding-window rate limiter
(default 40 requests per 10 seconds, below the provider's documented 50/10s
limit), bounded retries for network/429/5xx responses, and request accounting.
Fetch work can run concurrently, but database writes are performed on the
calling thread inside short transactions protected by `DATABASE_SCHEDULER_LOCK`.
This is deliberate: it serializes SQLite writers and avoids holding a database
write lock while waiting for network responses. A process-local `SYNC_LOCK`
prevents overlapping syncs initiated by the page, scheduler or management
command within the same process. `MFSyncRun` stores status, trigger, scope,
progress, statistics and errors; stale `running` records are closed as failed
when a later sync starts.

## Frontend behavior and access

The page is a standalone Angular feature at `/mf-intelligence`. It loads category groups, category dashboards and scheme detail from the MF Intelligence API. The page exposes the Overview, Fund Scorecard, Quant Score, Performance, Calendar Year, Risk & Rolling, Portfolio, Positioning, Universe Table and Manager Activity views.

The frontend observes the document root's theme class. When the application switches between light and dark mode, existing Chart.js instances are rebuilt so chart axes, labels, legends and other theme-dependent elements remain legible. Semantic status badges use theme-aware contrast rather than relying on a single light-theme palette.

The sync controls are hidden from Viewer users; the backend remains the authority for sync permissions. A page-level control is not a substitute for API authorization.

## Operational checks

- Check the last sync status on the page before initiating another full sync.
- Use `python manage.py sync_mf_intelligence --help` to review category, plan, worker, limit and skip-step options.
- Use `--limit` for a bounded test run and `--skip-*` options to isolate a sync stage when diagnosing provider or persistence issues.
- Confirm that refreshed records are present in the database; the feature is designed around database-backed master/reference tables, not a frontend cache or a local data folder.
- The optional daily sync remains disabled unless explicitly enabled in `pwms_config.json` or `PWMS_CONFIG_OVERRIDES`.

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
