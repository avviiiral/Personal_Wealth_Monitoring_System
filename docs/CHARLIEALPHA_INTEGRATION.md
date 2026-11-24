# CharlieAlpha mutual-fund integration

This integration is an opt-in extension to the existing PWMS global AMFI / mutual-fund master-data pipeline. It does not replace user-owned investor schemes, transactions, or portfolio data. CharlieAlpha observations are linked to the existing global AMFIMasterScheme universe.

## Data sources and storage

Provider base URL: https://api.charliealpha.in/v1

The public API documentation describes unauthenticated GET endpoints and a request limit of 50 requests per 10 seconds. The adapter serializes calls with a conservative 0.205-second minimum interval, uses connect/read timeouts, retries transient failures and HTTP 429 responses, and does not call the provider at Django startup.

| Data domain | CharlieAlpha endpoint | PWMS persistence |
| --- | --- | --- |
| Discovery | /mf/categories, /mf/schemes, /mf/search | MFSchemeIdentityMap for confirmed AMFI identity links; unmatched/search details in MFSourceRun.metadata |
| Latest NAV | /mf/lnavall, /mf/lnav/{code} | AMFIMasterNAV (source=CHARLIEALPHA) and current MFMasterData snapshot; per-scheme NAV change metadata in MFSourceRun |
| NAV history | /mf/hnav/{code} | AMFIMasterNAV (source=CHARLIEALPHA); daily rows do not create daily MFMasterData snapshots |
| Performance/risk | /mf/metrics/{code}, /mf/metrics-by-cat/{slug} | Scalar fields supported by MFMasterData; full rolling CAGR/XIRR horizon distributions, category medians and sample metadata in MFSourceRun.metadata |
| TER/AUM | /mf/ter/{code}, /mf/aum/{code} | Dated MFMasterData snapshots; Direct/Regular TER remains plan-specific |
| Holdings | /mf/holdings/{code} | MFMasterHolding, MFExternalPortfolioSnapshot, and MFMasterData holdings summary; security keys are prefixed CHARLIEALPHA: |
| Holdings history | /mf/holdings-periods, /mf/holdings-periods/{code}, /mf/holdings/{code}?as_of=... | Dated MFMasterHolding disclosure snapshots and MFSourceRun disclosure-period metadata |
| Security exposure | /mf/fund-exposure/{identifier} | Provider response retained in database-backed MFSourceRun.metadata |
| Compare/overlap | /mf/compare, /mf/overlap | Provider comparison response retained in database-backed MFSourceRun.metadata |
| Benchmarks | /benchmark/list, /benchmark/hnav/{slug} | MFProviderBenchmark and MFProviderBenchmarkObservation, including normalization base date and method |

CharlieAlpha benchmark history is described as normalized to 100 at the beginning of the returned period. These observations are deliberately stored separately from MFBenchmarkTRI; they must not be treated as absolute official TRI levels or used as such in existing PWMS benchmark-relative return calculations.

## Scheme matching

The adapter maps provider scheme identifiers to existing AMFIMasterScheme rows by:
1. Exact AMFI scheme code.
2. Exact ISIN match.
3. Unique exact scheme-name match.

Ambiguous or unmatched records are logged to the synchronization run and are not fuzzy-matched or inserted as unverified scheme masters. A provider scheme ID that differs from the AMFI code is retained in MFSchemeIdentityMap.

## Management command

Run from backend/ with the project virtual environment active.

~~~~powershell
# Discover / map schemes and categories
python manage.py sync_charliealpha --domain schemes

# Bulk latest NAV
python manage.py sync_charliealpha --domain nav

# Per-scheme latest NAV movement and accompanying metadata
python manage.py sync_charliealpha --domain nav-detail --scheme-code 120137

# Historical NAV for one scheme
python manage.py sync_charliealpha --domain nav-history --scheme-code 120137

# Category metrics (all four horizons when supplied by the provider)
python manage.py sync_charliealpha --domain metrics --category equity-multi-cap

# TER, AUM and latest holdings for one or more schemes
python manage.py sync_charliealpha --domain ter --scheme-code 120137
python manage.py sync_charliealpha --domain aum --scheme-code 120137
python manage.py sync_charliealpha --domain holdings --scheme-code 120137

# Historical holdings: latest four provider disclosure periods by default
python manage.py sync_charliealpha --domain holdings-history --scheme-code 120137 --period-limit 4

# Stock exposure, comparison, overlap and benchmarks
python manage.py sync_charliealpha --domain exposure --identifier HDFCBANK
python manage.py sync_charliealpha --domain compare --compare-code 120137 --compare-code 118834
python manage.py sync_charliealpha --domain overlap --compare-code 120137 --compare-code 118834
python manage.py sync_charliealpha --domain benchmarks

# Full current-data synchronization; --limit caps per-scheme calls
python manage.py sync_charliealpha --domain all --limit 10

# Full current-data synchronization for the linked universe
python manage.py sync_charliealpha --domain all

# Optional additional daily NAV history for the selected/limited universe
python manage.py sync_charliealpha --domain all --include-nav-history --limit 10

# Dry-run provider/response validation
python manage.py sync_charliealpha --domain all --limit 10 --dry-run
~~~~

--category takes a CharlieAlpha category slug when used for provider scheme/metrics filtering. --scheme-code is repeatable. --limit 0 means no artificial per-scheme cap. --period-limit 0 requests all disclosure dates returned for each scheme; use with care because it can generate a large number of provider calls.

The all workflow synchronizes scheme discovery/mapping, bulk latest NAV, category metrics, TER/AUM/latest holdings for linked schemes, global holdings-period coverage, and benchmark history. For a full historical holdings backfill, run --domain holdings-history; for daily historical NAV, use --include-nav-history. These are separate because they can generate many serial provider requests.

All runs write status/counters to MFSourceRun. The command reports fetch/insert/update/skip/failure/unmatched totals. Repeated runs use database upserts and the same source/scheme/date keys.

## Authenticated read APIs

All endpoints below require the normal PWMS authenticated session and are nested below /api/mutual-fund-analytics/charliealpha/.

- GET overview/ — paginated scheme snapshots plus search, AMC, category, plan and option filters, data coverage and latest ingestion status.
- GET funds/{scheme_code}/ — fund snapshot, filtered NAV history, TER/AUM history, a selected holdings report, provider metrics and data availability.
- GET funds/{scheme_code}/holdings-periods/ — persisted disclosure dates for the scheme.
- GET holdings-periods/ — latest persisted global disclosure coverage.
- GET exposure/{identifier}/ — previously ingested security exposure.
- GET benchmarks/ — provider-normalized benchmark observations with base-date provenance.
- GET categories/{slug}/ — persisted provider category medians.
- POST compare/ — explicitly requested two-to-twenty-fund provider comparison/overlap.

The frontend reads these PWMS endpoints only. It does not call CharlieAlpha from the browser.

## Data quality and limitations

- Missing fields remain null / absent; the frontend displays an em dash instead of a fabricated zero.
- A failed holdings call or an empty/invalid holdings payload is reported as unavailable, not as an empty or zero-weight portfolio.
- Holdings snapshots are dated disclosures, not daily portfolio states.
- The scalar risk fields in MFMasterData are provider supplied; the complete horizon-specific payload remains available in the provider run metadata. Pearson correlation is not mislabeled as R-squared.
- Provider benchmark values are normalized observations, not absolute benchmark TRI levels.
- CharlieAlpha public docs describe educational/research use and include a data-accuracy disclaimer. I did not find explicit retention, redistribution, or permanent-storage licence terms in the public API/methodology pages reviewed. Obtain and document the provider's written storage/retention rights before treating the data as permanently reusable in a production service. This integration persists the structured observations required for PWMS operation, but does not imply a licence grant.
- Manager name, exit load, riskometer, minimum investment and other scheme-document fields are not invented when absent from CharlieAlpha responses. Extend ingestion from an authoritative AMFI/AMC/SEBI source if those fields are required.
~~~~