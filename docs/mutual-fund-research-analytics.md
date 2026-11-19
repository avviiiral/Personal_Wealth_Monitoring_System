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

Mutual Fund Analytics endpoints are mounted under `/api/mutual-fund-analytics/`, separate from the Watch List API namespace. The report API keeps dated source snapshots separate from live Watch List NAV/performance data. Personal portfolio analytics reads only the existing family-scoped MutualFundHolding and MutualFundTransaction ledgers. It does not combine the generic investment Transaction ledger or create duplicate transactions.

## Data sources

AMFI remains the keyless primary source for scheme identity and NAV history. The personal portfolio endpoint uses the latest stored scheme NAV; if no NAV is available, it falls back to stored holding valuation and labels the source.

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
