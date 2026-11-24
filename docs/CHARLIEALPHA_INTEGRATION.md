# CharlieAlpha Mutual Fund & ETF endpoint integration

This is an opt-in data-source integration for the PWMS Mutual Fund Intelligence area. It covers all endpoint families listed by the provider's public catalogue: Mutual Funds & ETFs, NPS, SIF, GIFT City (IFSC) funds, Instruments, Corporate Actions, and Benchmarks. The non-MF families have different identifiers and data contracts; their payloads are stored in a separate provider-resource snapshot model and are never inserted into AMFI mutual-fund scheme tables or investor-owned holdings.

Provider API catalogue: https://api.charliealpha.in/
Methodology: https://api.charliealpha.in/methodology
Base URL: `https://api.charliealpha.in/v1`

The public catalogue describes an unauthenticated, GET-only API limited to 50 requests per 10 seconds. The adapter sends `Accept: application/json`, serializes requests with a 0.205-second minimum interval, applies finite connect/read timeouts, and retries HTTP 429 and transient 5xx/network failures. The integration never requests the provider from Django startup and never calls CharlieAlpha directly from the browser.

## Endpoint-by-endpoint coverage

| # | Provider endpoint | Documented behavior and parameters | PWMS implementation |
| ---: | --- | --- | --- |
| 1 | `GET /mf/categories` | Category tree grouped by Equity, Debt, Hybrid, ETF, Solution Oriented and Other; returns canonical slugs | `sync_categories()` stores the full response, flattened category records, asset-class labels and slugs in `MFSourceRun.metadata`; authenticated read endpoint exposes the stored catalogue |
| 2 | `GET /mf/schemes?cat={slug}` | Active mutual-fund and ETF directory; optional category slug filter | `sync_schemes()` resolves exact identities against AMFI; fills only missing AMC/category/plan/option fields, never overwrites known AMFI identity values |
| 3 | `GET /mf/search?q={query}` | Search by scheme name, code, ISIN, AMC or category; compact `fields` plus 2D `data` rows | `search_schemes()` decodes compact rows and persists query/results in the database audit record; management command only |
| 4 | `GET /mf/lnavall` | Bulk latest NAV for active schemes; compact `fields` plus 2D `data` rows | `sync_navall()` upserts provider-specific `AMFIMasterNAV` rows and latest `MFMasterData` NAV fields; it does not replace AMFI NAV rows |
| 5 | `GET /mf/lnav/{code}` | Per-scheme NAV, previous-day movement, date, AMC, category, plan, option, latest TER and quarterly-average AUM; AMFI code or ISIN | `sync_latest_nav_details()` persists NAV where available and keeps movement/extra metadata in `MFSourceRun.metadata`; dedicated TER/AUM endpoints remain the dated history source |
| 6 | `GET /mf/hnav/{code}` | Daily NAV `dates` and `nav` arrays plus scheme metadata; AMFI code or ISIN | `sync_nav_history()` validates paired arrays and upserts daily provider rows into `AMFIMasterNAV`; daily rows do not create daily master snapshots |
| 7 | `GET /mf/metrics/{code}` | 1Y/3Y/5Y/10Y trailing CAGR, rolling CAGR/XIRR distributions, risk metrics, correlation and fund age; AMFI code or ISIN | `sync_metrics(scheme_codes=[...])` maps supported scalar fields to `MFMasterData`; full horizons, percentiles, medians and sample counts remain in `MFSourceRun.metadata` |
| 8 | `GET /mf/metrics-by-cat/{slug}?horizon={1y|3y|5y|10y}` | Category-wide performance/risk metrics and category medians; optional horizon filter | `sync_metrics(category_slugs=[...], horizon=...)` stores scheme snapshots and a separate category-median run; no-horizon mode retains the complete multi-horizon provider payload |
| 9 | `GET /mf/ter/{code}` | Dated TER revisions as `dates` and `ter`, filtered to the scheme's Direct or Regular plan; AMFI code or ISIN | `sync_ter()` persists dated TER snapshots; classification is based on the resolved AMFI scheme plan and ambiguous plans are skipped rather than guessed |
| 10 | `GET /mf/aum/{code}` | Quarterly-average AUM history in ₹ crore; AMFI code or ISIN | `sync_aum()` stores dated values with `AUM_QUARTERLY_AVG` basis; values are not relabelled month-end AUM |
| 11 | `GET /mf/compare?codes=...` | 2–20 comma-separated AMFI codes/ISINs; summary holdings overlap is returned by default; `include_pairwise=true` adds return-correlation and holdings-overlap matrices | `compare()` stores the provider response and exact request parameters; pairwise data is requested only when explicitly enabled |
| 12 | `GET /mf/overlap?codes=...` | ISIN-matched holdings overlap for 2–20 funds; may return partial results/excluded schemes and mixed-report-month warnings; provider default controls pairwise output | `compare(overlap_only=True)` stores the response, partial-result indicator and report-date warning; provider HTTP errors are audited |
| 13 | `GET /mf/holdings/{code}?as_of={date}&change=yes` | Latest or selected-date holdings; optional previous-report changes and exited holdings; AMFI code or ISIN | `sync_holdings()` persists dated holdings, security metadata, provider changes and quality flags; empty or invalid holdings are marked unavailable/partial, never interpreted as a zero portfolio |
| 14 | `GET /mf/holdings-periods` | Global disclosure coverage, report dates, counts and latest report date | `sync_holdings_periods()` stores the provider coverage response as a global audit record |
| 15 | `GET /mf/holdings-periods/{code}` | Scheme-specific disclosure dates and holdings counts; AMFI code or ISIN | `sync_holdings_periods(codes)` and `sync_holdings_history()` store provider identifiers, canonical AMFI code, report periods and historical dated holdings |
| 16 | `GET /mf/fund-exposure/{identifier}?min_pct={value}` | Funds holding a stock; identifier may be stock ISIN, NSE symbol or BSE scrip code; optional minimum portfolio percentage | `sync_fund_exposure()` stores the provider's compact columns/data payload and normalized threshold in the database; read API matches thresholds numerically |
| 17 | `GET /benchmark/list` | Canonical benchmark names, slugs and asset categories | `sync_benchmarks()` reads the benchmark catalogue and fetches each selected series |
| 18 | `GET /benchmark/hnav/{slug}?range={window}` | Daily benchmark series rebased to 100 at the first observation; ranges ALL, 1M, 6M, 1Y, 3Y, 5Y, 10Y, 15Y, 20Y, 25Y or exact `from`/`to` dates | `sync_benchmarks(range_name=...)` or exact `from_date`/`to_date` stores the provider-normalized series in provider-specific benchmark tables with its base date, range and source URL |
| 19 | `GET /nps/schemes` | Active NPS scheme directory with scheme code, name and PFM | `sync_nps()` stores the directory response and normalized rows as an NPS resource snapshot |
| 20 | `GET /nps/lnav/{code}` | Latest NPS NAV, change, date, PFM, tier and asset class; accepts an NPS scheme code | `sync_nps()` stores each scheme response separately by NPS code |
| 21 | `GET /nps/hnav/{code}` | Daily NPS NAV history plus inception, latest NAV, tier and PFM | `sync_nps()` stores the original payload and parsed date/NAV rows |
| 22 | `GET /nps/metrics/{code}?horizon={period}` | Trailing/rolling performance and risk; optional horizon from 3M through 15Y | `sync_nps(horizon=...)` stores the raw metrics object and any returned structured records |
| 23 | `GET /sif/schemes` | Active SIF directory with AMC, category, plan, option, ISIN, average AUM and 3M–10Y return metrics | `sync_sif()` stores the full SIF directory snapshot |
| 24 | `GET /sif/lnavall` | Compact bulk latest NAV for active SIF schemes | `sync_sif()` stores the bulk NAV table as normalized rows |
| 25 | `GET /sif/search` | Real-time SIF scheme search by name, code, ISIN, AMC or category | `sync_sif(search_query=...)` stores the returned search snapshot |
| 26 | `GET /sif/lnav/{code}` | Latest NAV and movement; accepts SIF ID or ISIN | `sync_sif()` stores per-scheme latest-NAV responses using the SIF identifier |
| 27 | `GET /sif/hnav/{code}` | Daily SIF NAV history and scheme metadata; accepts SIF ID or ISIN | `sync_sif()` stores paired NAV history rows without linking the identifier to AMFI |
| 28 | `GET /gift/schemes?compact=false` | GIFT City fund directory, legal entity, AMC brand, asset class, investor tier, flow direction, minimum investment, currency and share classes; compact labels by default | `sync_gift()` requests expanded JSON and stores the GIFT directory snapshot |
| 29 | `GET /gift/search?q={query}&compact=false` | Search GIFT funds by fund/AMC/legal entity, asset class, ISIN or numeric ID | `sync_gift(search_query=...)` stores the search response with its request parameters |
| 30 | `GET /gift/lnavall` | Compact bulk latest NAV across active GIFT City funds and plan variants | `sync_gift()` stores the bulk NAV rows without creating AMFI schemes |
| 31 | `GET /gift/lnav/{code}` | Latest NAV, previous NAV and movement across plan variants; numeric fund ID or ISIN | `sync_gift()` stores the fund-level response with its GIFT identifier |
| 32 | `GET /gift/hnav/{code}?plan={plan}&start_date={date}&end_date={date}` | Daily NAV series across plan variants by default; optional plan and independent date boundaries | `sync_gift(plan=..., from_date=..., to_date=...)` stores each plan-keyed series as rows preserving the plan label |
| 33 | `GET /instruments/search?q={query}&type={type}&limit={n}` | Search Indian equities, bonds, ETFs, G-secs and other instruments; type filter and limit 1–100 | `search_instruments()` stores normalized result rows and the raw response |
| 34 | `GET /instruments/{isin}` | Instrument details by 12-character ISIN, including issuer, exchange identifiers, instrument type and available security terms | `instrument_details()` stores the instrument response under the INSTRUMENTS family |
| 35 | `GET /instruments/corp-act?code=...&status=...&action_type=...&from_date=...&to_date=...&limit=...` | Stock/ETF splits, face-value consolidations and bonus issues; status all/applied/upcoming, action ALL/SPLIT/BONUS, optional dates and limit 1–200 | `corporate_actions()` stores the response as a CORPORATE_ACTIONS snapshot with the exact filter parameters |

### Important response-shape rules

- The provider uses both object arrays and columnar responses with root-level `fields` and 2D `data`. The adapter decodes both forms.
- NAV, TER and AUM history endpoints use paired date/value arrays. PWMS records unequal array lengths as an ingestion issue and does not silently imply complete coverage.
- Benchmark values already arrive normalized to 100 for the requested time window. PWMS **does not rebase them a second time**. Each returned window is stored separately by its normalization base date.
- Compare and overlap are distinct endpoints. The API's documented defaults are preserved unless `--include-pairwise` is explicitly supplied.
- Provider-returned Pearson correlation is not mapped to R-squared. Metrics that have no semantically equivalent PWMS scalar field remain in the raw structured horizon metadata.
- The UI's overview merges the most recent non-null NAV, performance/risk, TER, AUM and holdings values per domain. It does not assume those domains were all reported on the same date.

## Database and identity model

CharlieAlpha rows link to the existing global `AMFIMasterScheme` universe. Identity resolution uses exact AMFI code, exact ISIN, an existing confirmed CharlieAlpha identity map, or a unique exact scheme name. Ambiguous identities and code/ISIN conflicts are rejected and recorded; no fuzzy match creates a scheme link.

Investor-owned schemes, transactions, SIPs and portfolio holdings remain separate from the global fund research universe.

Persistence is deliberately source-aware:

- NAV history: `AMFIMasterNAV`, source `CHARLIEALPHA`.
- Dated scalar metrics, TER, AUM, and holdings summary: `MFMasterData`, with field-level source/date provenance.
- Security disclosures: `MFMasterHolding`, with `CHARLIEALPHA:`-prefixed security keys.
- Provider portfolio report metadata: `MFExternalPortfolioSnapshot`.
- Provider benchmarks: `MFProviderBenchmark` and `MFProviderBenchmarkObservation`, never `MFBenchmarkTRI`.
- Provider request/result distributions, searches, comparisons, categories and operational errors: `MFSourceRun.metadata` JSON fields in the configured database.
- NPS, SIF, GIFT City, instrument search/detail and corporate-action responses: versioned `MFProviderResourceSnapshot` rows in `mf_provider_resource_snapshots`. Each record includes provider family, endpoint, provider-native identifier, request parameters, original JSON payload, normalized tabular rows, record count, as-of date (when supplied), source URL, SHA-256 payload hash, fetch timestamp and success/error state.
- Extended-family ingestion runs are also written to `MFSourceRun`; the read endpoint exposes the saved snapshots without invoking CharlieAlpha from the browser.

## Management command

Run from `backend/` with the project virtual environment active.

```powershell
# Store the category catalogue and discover/map the active AMFI-linked universe
python manage.py sync_charliealpha --domain categories
python manage.py sync_charliealpha --domain schemes

# Search and bulk/per-scheme NAV
python manage.py sync_charliealpha --domain search --search-query "large cap"
python manage.py sync_charliealpha --domain nav
python manage.py sync_charliealpha --domain nav-detail --scheme-code 120137
python manage.py sync_charliealpha --domain nav-history --scheme-code 120137

# All horizons, or one category horizon
python manage.py sync_charliealpha --domain metrics --category equity-large-cap
python manage.py sync_charliealpha --domain metrics --category equity-large-cap --horizon 3y
python manage.py sync_charliealpha --domain metrics --scheme-code INF769K01BI1

# Dated TER, quarterly-average AUM and latest/historical holdings
python manage.py sync_charliealpha --domain ter --scheme-code 120137
python manage.py sync_charliealpha --domain aum --scheme-code INF769K01BI1
python manage.py sync_charliealpha --domain holdings --scheme-code 120137
python manage.py sync_charliealpha --domain holdings --scheme-code 120137 --skip-holdings-changes
python manage.py sync_charliealpha --domain holdings-history --scheme-code 120137 --period-limit 4

# Stock exposure and fund compare/overlap
python manage.py sync_charliealpha --domain exposure --identifier HDFCBANK --min-pct 2.5
python manage.py sync_charliealpha --domain compare --compare-code 120137 --compare-code 118834
python manage.py sync_charliealpha --domain compare --compare-code 120137 --compare-code 118834 --include-pairwise
python manage.py sync_charliealpha --domain overlap --compare-code 120137 --compare-code 118834

# Benchmark series for a relative range or exact dates
python manage.py sync_charliealpha --domain benchmarks --benchmark-range 5Y
python manage.py sync_charliealpha --domain benchmarks --benchmark-from 2021-01-01 --benchmark-to 2025-12-31

# Full current-data synchronization; --limit caps per-scheme/benchmark calls
python manage.py sync_charliealpha --domain all --limit 10
python manage.py sync_charliealpha --domain all

# Optional daily NAV-history backfill for a limited universe
python manage.py sync_charliealpha --domain all --include-nav-history --limit 10

# Validate a bounded provider pull without writing scheme data
python manage.py sync_charliealpha --domain all --limit 10 --dry-run

# NPS: scheme directory, latest NAV, NAV history and metrics (scheme IDs are NPS-native)
python manage.py sync_charliealpha --domain nps --limit 10
python manage.py sync_charliealpha --domain nps --limit 10 --nps-horizon 3y

# SIF: directory, bulk NAV, optional search and per-scheme NAV/history
python manage.py sync_charliealpha --domain sif --limit 10
python manage.py sync_charliealpha --domain sif --limit 10 --search-query "long short"

# GIFT City: expanded directory, optional search, bulk and per-fund/plan NAV
python manage.py sync_charliealpha --domain gift --limit 10
python manage.py sync_charliealpha --domain gift --limit 10 --search-query "global equity" --gift-plan Direct --gift-from 2024-01-01 --gift-to 2026-11-24

# Instruments and corporate actions (ISINs and exchange identifiers remain provider-native)
python manage.py sync_charliealpha --domain instrument-search --search-query "Reliance Industries" --instrument-type EQUITY --limit 50
python manage.py sync_charliealpha --domain instrument-detail --identifier INE002A01018
python manage.py sync_charliealpha --domain corporate-actions --corporate-code RELIANCE --corporate-status upcoming --action-type SPLIT --limit 50

# Optional bulk run across non-MF provider families; always start with a conservative limit
python manage.py sync_charliealpha --domain provider-all --limit 10
python manage.py sync_charliealpha --domain provider-all --limit 10 --dry-run
```

`--scheme-code` accepts a six-digit AMFI code or a 12-character mutual-fund ISIN and is repeatable. `--category` is the provider category slug. `--horizon` applies to category metrics and accepts 1y, 3y, 5y or 10y. `--benchmark-from` and `--benchmark-to` must be supplied together. `--period-limit 0` requests every disclosure date returned for each scheme and can generate many calls. The default pairwise setting is left to the provider; pass `--include-pairwise` to request pairwise output explicitly. For the extended families, `--nps-horizon` is passed to NPS metrics (the provider documents periods from 3M to 15Y); SIF and NPS identifiers are not treated as AMFI codes. GIFT `--gift-from` and `--gift-to` are independent optional history bounds, while a supplied `--gift-plan` selects a plan series. Corporate-action date filters must be supplied as a pair. `--domain provider-all` fetches NPS/SIF/GIFT directories and selected scheme detail, optionally searches instruments when `--search-query` is present, and requests corporate actions; use `--limit` to bound per-fund calls. Instrument details require a 12-character ISIN via `--identifier`.

The `all` domain stores categories/scheme mapping, bulk NAV, category metrics, latest NAV details for explicitly supplied codes, linked-scheme TER/AUM/holdings, global holdings-period coverage, and benchmark history. Daily NAV history and full holdings-history backfills remain opt-in because they can make a large number of serial provider calls. The separate `provider-all` domain covers the non-MF families; it is deliberately separate from `all` so an existing MF refresh does not unexpectedly expand into NPS/SIF/GIFT/instrument calls.

## Authenticated PWMS read APIs

All routes are below `/api/mutual-fund-analytics/charliealpha/` and require the normal PWMS authenticated session:

- `GET overview/` — paginated scheme universe, search/AMC/category/plan/option filters, coverage and ingestion status.
- `GET categories/` — stored category tree, canonical slugs and asset-class grouping.
- `GET categories/{slug}/?horizon=3y` — stored category medians and sample metrics for a horizon; falls back to an all-horizons ingestion when present.
- `GET funds/{scheme_code_or_isin}/` — fund metadata, latest values by data domain, date-filtered NAV, TER/AUM history, selected holdings, provider metrics and availability.
- `GET funds/{scheme_code}/holdings-periods/` and `GET holdings-periods/` — stored scheme/global report coverage.
- `GET exposure/{identifier}/?min_pct=...` — previously ingested stock exposure.
- `GET benchmarks/?slug=...` — latest stored normalized benchmark window with base-date provenance.
- `POST compare/` — read a previously ingested 2–20 fund compare/overlap response. The browser never triggers a live provider request.
- `GET provider-data/?family=NPS|SIF|GIFT|INSTRUMENTS|CORPORATE_ACTIONS&limit=...` — read saved non-MF resource snapshots, their normalized rows, request metadata, original payload, provenance, and ingestion status.

## Data quality, limitations and licensing

- Missing fields remain null/absent; the UI does not convert unavailable data to zero.
- Invalid or empty holdings are marked unavailable/partial and do not replace a valid historical disclosure with an empty portfolio.
- Holdings remain dated provider disclosures, not daily portfolio states. Historical report periods are persisted.
- Bond/fixed-income holding fields such as rating, rating agency, maturity date and yield-to-maturity are retained where supplied. Asset/sector allocations are only populated from classified rows with a supplied weight.
- Rolling return/XIRR distributions and percentiles remain provider payloads; scalar fields are populated only when a matching PWMS field and metric definition exist.
- Benchmark observations remain separate from official benchmark TRI. Never compare a normalized provider value directly with an absolute TRI level.
- NPS, SIF and GIFT City IDs, performance/NAV values and instrument ISINs remain in provider-family snapshots. They are not linked to `AMFIMasterScheme`, and no personal portfolio position is created from them.
- The public site states that the API is for education/research and disclaims responsibility for investment losses or data inaccuracy. The reviewed public pages do not establish explicit permanent-retention or redistribution rights. Obtain written provider terms before production retention or redistribution. This implementation does not imply a licence grant.

