# Mutual Fund Analytics — Research and Portfolio Dashboard

## Research report source contract

The importer reads three JSON objects embedded in the supplied Nexedge HTML:

- RAW: category reference metrics, fund-level scorecard fields, market-cap/style allocation, monthly trend arrays, sector weights, top holdings and unique ideas.
- P2P: point-to-point returns, coverage counts, category returns and benchmark values.
- ATTR: per-fund stock contributors/laggards, sector attribution and cross-fund gainers/laggards.

The importer parses the data objects as JSON. It does not execute JavaScript or import the HTML as executable code.

## Import

From backend:

```powershell
python manage.py migrate
python manage.py import_mutual_fund_research_report --file "D:\path\to\Nexedge_multicap_dashboard_Oct2026.html"
```

Optional report-key allows multiple report families to be stored independently. The same key/date pair is updated on re-import. New report dates are retained as separate snapshots.

## APIs

Authenticated endpoints:

| Endpoint | Method | Purpose |
| --- | --- | --- |
| /api/mutual-fund-analytics/research-report/ | GET | Latest imported RAW/P2P/ATTR research snapshot |
| /api/mutual-fund-analytics/india-market-feed/ | GET | Cached IndianAPI snapshot or keyless AMFI scheme/NAV fallback |
| /api/mutual-fund-analytics/api-usage/ | GET | PWMS-side monthly IndianAPI request ledger |
| /api/mutual-fund-analytics/refresh/ | POST | Explicit cache-first refresh; write-capable financial role required |
| /api/mutual-funds/analytics/portfolio/ | GET | Active-family holdings, XIRR and latest disclosed exposures |

Mutual Fund Analytics endpoints are mounted under `/api/mutual-fund-analytics/`, separate from the Watch List API namespace. The report API keeps dated source snapshots separate from live Watch List NAV/performance data. Personal portfolio analytics reads family-scoped MutualFundHolding/MutualFundTransaction records and also supports legacy Asset/Holding/Transaction positions explicitly tagged as mutual funds. Legacy positions are de-duplicated by exact ISIN or exact normalized name; ambiguous positions without ISINs are excluded and reported rather than double-counted.

## Data sources

AMFI remains the keyless primary source for scheme identity and NAV history. The personal portfolio endpoint uses the latest stored scheme NAV for dedicated schemes and the global AMFI master NAV for legacy Asset-based mutual funds mapped by ISIN. If no NAV is available, it falls back to the stored family holding valuation (or cost basis when no stored price exists) and labels the source.

IndianAPI /mutual_funds is only used for the Mutual Fund Analytics enrichment snapshot. It is not called by Watch List refreshes, dashboard page loads, tab switches, filter changes or GET endpoints. The explicit refresh endpoint returns a cached snapshot without provider access when it is less than 24 hours old. Failed refreshes preserve the last good snapshot.

## Monthly request protection

- INDIANAPI_MONTHLY_REQUEST_BUDGET defaults to 400 and is clamped so PWMS cannot dispatch more than 400 requests in a calendar month.
- The persistent monthly ledger stores attempts, last request timestamp, refresh status and refresh errors.
- A request is reserved before network I/O. Failed dispatched attempts count conservatively.
- A recent in-progress reservation blocks a duplicate simultaneous refresh. A stale marker can be recovered after 15 minutes.
- There are no automatic retry loops or polling. Cache hits consume zero requests.
- The displayed ledger is PWMS-side accounting. IndianAPI account dashboard remains authoritative for provider billing and actual quota.

Environment:

```env
INDIANAPI_API_KEY=
INDIANAPI_BASE_URL=https://stock.indianapi.in
INDIANAPI_TIMEOUT_SECONDS=20
INDIANAPI_MONTHLY_REQUEST_BUDGET=400
```

Keep the API key server-side. PWMS remains usable without a key; AMFI and previously cached data remain available.

## Calculation conventions

For portfolio XIRR, purchases and SIPs are negative cash flows including fees; redemptions and dividends are positive cash flows net of fees; current market value is the terminal positive cash flow at the valuation date. XIRR is annualized and returned only when the existing calculator can solve a valid cash-flow series.

Unrealized gain/loss is current market value less cost basis of current units. The simple total-return indicator is (current market value + redemption proceeds + dividends - gross purchase outflow) / gross purchase outflow. It is not annualized and should not be compared with CAGR.

Sector and stock exposure uses the latest stored underlying disclosure for each held scheme. The disclosure date is returned separately. The dashboard does not infer portfolio changes or attribution from a single snapshot. Missing observations remain unavailable rather than being fabricated.

## Known limitations

- The original report's EDGE formula and attribution methodology must be verified against the exact HTML source before representing either as a PWMS-calculated result. Imported values remain source-reported snapshots.
- AMFI NAV history supports return calculations only where the required dates exist.
- Portfolio disclosure coverage and freshness depend on the existing underlying ingestion pipeline.
- PWMS quota accounting cannot observe provider-side adjustments or use outside this application.


## SEBI universe and historical NAV analytics

The SEBI research pipeline is independent of IndianAPI and does not consume the 400-request IndianAPI allowance.

### Sources and boundaries

- AMFI scheme master: `https://www.amfiindia.com/spages/NAVAll.txt` (semicolon-delimited NAVAll feed). The ingestion retains the AMFI AMC header, raw category header, scheme code, both ISIN columns, scheme name, NAV/date and inferred or explicit plan/option fields.
- Historical NAV: MFapi.in documented endpoint `https://api.mfapi.in/mf/{scheme_code}`. Its full JSON response is cached under `backend/data_cache/sebi_mf_analytics/nav/`; normal builds use local cache. `--refresh-cache` explicitly bypasses it.
- Current category mapping: SEBI Circular HO/24/13/15(2)2026-IMD-RAC4/I/5764/2026 dated 26 February 2026. It defines 40 current leaf categories: 13 Equity, 17 Debt, 7 Hybrid, 1 Life Cycle and 2 Other Schemes. Solution Oriented is discontinued and remains only as a legacy classification for older feed labels.
- Monthly holdings disclosures: user-supplied AMC/AMFI `.xlsx` files with ISIN and `% to Net Assets`. Files are cached locally; no scraping or invented holdings are performed.

### Build commands

From the `backend` directory:

```powershell
python manage.py migrate
python manage.py ingest_sebi_mf_universe
python manage.py build_sebi_mf_analytics
```

Direct Plan - Growth is the default analysis universe. To evaluate Direct and Regular Growth as separate peer groups, run:

```powershell
python manage.py build_sebi_mf_analytics --plans both
```

Optional constraints/settings:

```powershell
python manage.py build_sebi_mf_analytics --categories "Large Cap" "Flexi Cap" --max-schemes 100 --workers 4 --requests-per-second 1.0
python manage.py build_sebi_mf_analytics --refresh-cache
python manage.py build_sebi_mf_analytics --risk-free-rate 6.5 --expense-ratio-file "D:\\data\\scheme_ter.xlsx" --category-flow-file "D:\\data\\category_flows.xlsx"
```

The worker pool is bounded to 16 threads and outbound MFapi requests share a thread-safe rate limiter (default one request/second). Failed requests use bounded retries; valid cached responses are reused. The analytics command writes:

- `backend/exports/SEBI_Universe_MF_Analytics.xlsx`
- `backend/exports/SEBI_Category_Return_vs_Risk.png`
- `backend/exports/SEBI_Top_Q1_vs_Category_Average.png`

The workbook includes `Tree_Summary` and `Fund_vs_Category_Rankings`; `Portfolio_Overlap` is added when monthly portfolio disclosures have been imported. TER and category AUM/net flow columns remain empty unless corresponding optional source files are supplied.

### Performance, peer and risk conventions

- Trailing CAGR: 1Y, 3Y, 5Y and 10Y, using the latest stored NAV and the nearest available NAV observation on/before the target anniversary date. Missing history remains null.
- Rolling consistency: each observed NAV date is an endpoint for a 3-year trailing CAGR when a valid start NAV exists; mean, minimum and maximum are reported.
- Risk ratios: annualized daily NAV volatility, Sharpe and Sortino are calculated over the most recent three-year NAV window (or all available observations for younger schemes). The annual risk-free rate defaults to 6.5%; the window start and observation count are included in the fund rows.
- Maximum drawdown and peak-to-trough duration/recovery use the full available NAV history.
- Category average and median are grouped by exact Level 3 category and plan. Outperformance is the 3Y CAGR difference from the corresponding peer average, multiplied by 100 to express basis points. Percentiles and quartiles are assigned within that same category/plan group.
- The simple CAGR/volatility/risk-ratio outputs are NAV series metrics; they do not include investor cash-flow timing or personal portfolio transactions. Use the separate My Portfolio section for actual family-ledger XIRR.

### Monthly portfolio overlap

Import each scheme disclosure by its AMFI scheme code:

```powershell
python manage.py import_sebi_mf_portfolio --file "D:\\disclosures\\fund_portfolio.xlsx" --scheme-code 100001 --portfolio-date 2026-10-31
```

The actual disclosure date is required explicitly. The importer never treats a file's modification timestamp as the portfolio's as-of date.

Pairwise overlap is computed only for schemes in the same Level 3 category using common ISINs and the sum of minimum disclosed weights. The implementation uses the latest cached disclosure per scheme. It is a point-in-time pairwise research comparison, not the SEBI-prescribed quarterly average of daily overlap values used for regulatory compliance disclosures.

### Cached APIs

Authenticated read-only endpoints serve the last built snapshot and never call AMFI/MFapi/IndianAPI:

- `GET /api/mutual-fund-analytics/sebi/summary/`
- `GET /api/mutual-fund-analytics/sebi/rankings/?page=1&page_size=100&plan=Direct%20Plan`
- `GET /api/mutual-fund-analytics/sebi/export/`
- `GET /api/mutual-fund-analytics/sebi/charts/category-risk/`
- `GET /api/mutual-fund-analytics/sebi/charts/top-q1/`

The local filesystem cache and generated workbook are intentionally ignored by Git; deployments must run the ingestion/build commands on their own environment after migration.
