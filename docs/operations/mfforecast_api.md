# MF Forecast holdings API integration

## Endpoint and response contract

The supplied MF Forecast documentation screenshot shows:

- Method: `GET`
- Path: `/funds/{amfi_code}/holdings`
- Description: retrieve complete portfolio holdings for a mutual fund by AMFI scheme code.
- Response fields: `scheme_name`, `fund_house`, `category`, `aum_cr`, `expense_ratio`, `asset_allocation`, and `holdings[]`.
- Holding fields: `stock_name`, `ticker`, `isin`, `weight_pct`, `quantity`, and `asset_type`.

The screenshot does not specify the API base URL, authentication header, rate limit, portfolio disclosure date, or reuse/licensing terms. Configure the exact base URL from the provider's current documentation. Authentication is optional in the adapter; when required, configure both the API key and its documented header name. Do not guess a key header.

## Configuration

Add these values to the local `backend/.env` (never commit the real key):

~~~dotenv
MFFORECAST_API_BASE_URL=
MFFORECAST_API_KEY=
MFFORECAST_API_KEY_HEADER=
MFFORECAST_API_TIMEOUT_SECONDS=20
~~~

The route is appended as `/funds/{amfi_code}/holdings`. For example, `--scheme-code 128629` requests the AMFI scheme code 128629. The base URL should be the API origin/base prefix confirmed by MF Forecast support/docs.

## Run

The command requires an explicit scheme or AMC selector and defaults to at most 50 scheme requests. It does not run a full-universe fan-out accidentally.

~~~powershell
python manage.py sync_mfforecast_holdings --scheme-code 128629 --dry-run
python manage.py sync_mfforecast_holdings --scheme-code 128629
python manage.py sync_mfforecast_holdings --amc "HDFC" --limit 25 --dry-run
python manage.py sync_mfforecast_holdings --amc "HDFC" --limit 25
~~~

Requests are throttled to no more than one per second per host. Transient network/server errors and HTTP 429 are retried with bounded backoff. HTTP 404 is recorded as no provider record; HTTP 401/403 stops the run. Raw JSON is cached under `backend/data/mf_raw/mfforecast_api/<retrieval-date>/`, which is git-ignored.

## Storage and date semantics

- Each response's top-level fields are preserved in `MFExternalPortfolioSnapshot`, with its observation timestamp, source URL, raw-cache path, scheme name, fund house, category, AUM in crore, expense ratio percentage and asset allocation.
- Each holding is stored in `MFMasterHolding`, including ticker, ISIN, quantity, NAV weight, and normalized asset type. Identity matching uses the requested AMFI code; ISIN is retained at the security level.
- The example response has no holdings disclosure/as-of date. Consequently, the holding's `portfolio_date` is the retrieval date and `portfolio_date_basis` is `FETCHED_AT`. This is not represented as a confirmed month-end portfolio disclosure.
- Existing AMC/imported holdings default to `DISCLOSURE`. Masterdata analytics prefers the latest dated disclosure and only falls back to MF Forecast retrieval-date holdings when no dated disclosure exists.
- When the MF Forecast fallback is used, the quality flag `PORTFOLIO_DATE_IS_FETCH_DATE` is attached. The provider's asset-allocation percentages are used for allocation metrics when all three equity/debt/cash values are valid; a mismatch against row-derived buckets is flagged.
- Provider AUM and expense ratio are retained in the provider snapshot table, but are not copied into canonical `MFMasterData.aum_cr` or Direct/Regular TER fields. The screenshot does not establish an AUM effective date/type or a TER effective date/plan-specific definition.

## Data rights and operating checks

Before scheduling repeated or bulk collection, confirm that MF Forecast's current terms/API plan permit the intended call volume, storage and downstream use. Public documentation alone does not establish redistribution rights. Start with a few schemes, compare their reported holdings and weights with the relevant AMC disclosure, and inspect the `MFExternalPortfolioSnapshot.quality_flags` and source-run record. Do not scale to all 14,000+ AMFI rows until provider limits and data rights are confirmed.
