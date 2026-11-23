# MF Forecast holdings API integration

## Endpoint and response contract

The supplied MF Forecast documentation screenshot shows:

- Method: `GET`
- Path: `/funds/{amfi_code}/holdings`
- Description: retrieve complete portfolio holdings for a mutual fund by AMFI scheme code.
- Response fields: `scheme_name`, `fund_house`, `category`, `aum_cr`, `expense_ratio`, `asset_allocation`, and `holdings[]`.
- Holding fields: `stock_name`, `ticker`, `isin`, `weight_pct`, `quantity`, and `asset_type`.

The supplied endpoint URL/configuration uses RapidAPI. RapidAPI's standard authentication requires two separate headers: `X-RapidAPI-Key` (your app key) and `X-RapidAPI-Host` (the API host). PWMS now constructs both automatically for `.p.rapidapi.com` hosts. See [RapidAPI authentication documentation](https://docs.rapidapi.com/docs/configuring-api-security). The screenshot itself still does not establish the portfolio disclosure date or reuse/licensing terms.

## Configuration

Add these values to the local `backend/.env` (never commit the real key):

~~~dotenv
MFFORECAST_API_BASE_URL=https://indian-mutual-fund-portfolio-holding.p.rapidapi.com
MFFORECAST_API_KEY=your_rapidapi_app_key
MFFORECAST_API_KEY_HEADER=x-rapidapi-key
MFFORECAST_API_TIMEOUT_SECONDS=20
~~~

The route is appended as `/funds/{amfi_code}/holdings`. For example, `--scheme-code 128629` requests the AMFI scheme code 128629. Configure only the API host as the base URL. If a full `/funds/<code>/holdings` URL is supplied accidentally, PWMS strips that suffix and constructs the requested scheme route. The API key is the RapidAPI app key; do not put the host value in `MFFORECAST_API_KEY_HEADER`.

The live RapidAPI response observed for scheme 128629 uses `name` (not `stock_name`) for the security name, and includes top-level `amfi_code`, `holdings_count`, and a nested `snapshot` object. The parser accepts both `name` and the sample's `stock_name`, validates the response AMFI code against the requested code, and maps nested snapshot fields: `aum_crores` to provider AUM, `equity_pct`/`debt_pct`/`cash_pct` to provider allocation, and `snapshot_date` to `provider_as_of_date`.

The 128629 live response returned 102 holdings whose raw `weight_pct` values summed to `0.9555`. Because the provider count matched all 102 parsed rows, PWMS interprets this complete response as fractional NAV weights and multiplies each value by 100 before storing it as percentage points (for example, `0.0541` becomes `5.4100%`). This inference is deliberately limited to a complete, valid response whose raw total is greater than zero and no more than 1.05. A truncated or invalid response is not rescaled; a provider count mismatch is flagged and makes the source run `PARTIAL`. The inference is recorded with `MFFORECAST_WEIGHT_SCALE_INFERRED_FRACTION_TO_PERCENT`; the provider's numeric unit should still be confirmed with MF Forecast.

The provider also labelled multiple coupon-bearing or maturity-dated debt securities as `REIT`. PWMS overrides that label to `BOND` only when the security name contains coupon-rate or maturity-date evidence, preserving plain REIT labels otherwise. Overrides are recorded with `MFFORECAST_PROVIDER_ASSET_TYPE_OVERRIDDEN`. `DEBT` is normalized to bond; genuine `REIT` and `INVIT` classifications remain distinct and stay in the other-allocation bucket.

## Run

The command requires an explicit scheme or AMC selector and defaults to at most 50 scheme requests. It does not run a full-universe fan-out accidentally. For RapidAPI, it sends both `X-RapidAPI-Key` and `X-RapidAPI-Host` separately.

For a cross-AMC pilot, first generate a candidate list from the local AMFI scheme master. This planner makes no network calls and writes no rows. It chooses one active Direct-Growth scheme per AMC, favors categories not already represented in the pilot, and excludes schemes already having an MF Forecast snapshot.

~~~powershell
python manage.py plan_mfforecast_pilot
~~~

By default, it checks HDFC, SBI, ICICI Prudential, Axis and Nippon India. To choose different AMCs, repeat `--amc`:

~~~powershell
python manage.py plan_mfforecast_pilot --amc "HDFC" --amc "SBI" --amc "Kotak"
~~~

The planner prints exact AMFI scheme codes and a single bounded dry-run command. Review each response's provider count, parsed count, invalid rows, weight total, provider date, overrides and quality flags. The dry-run makes live API requests but does not write database records. Only after reviewing every scheme should you run the printed command without `--dry-run`.

~~~powershell
python manage.py sync_mfforecast_holdings --scheme-code 128629 --dry-run
python manage.py sync_mfforecast_holdings --scheme-code 128629
python manage.py sync_mfforecast_holdings --amc "HDFC" --limit 25 --dry-run
python manage.py sync_mfforecast_holdings --amc "HDFC" --limit 25
~~~

For example, the command accepts multiple explicit scheme selectors in one bounded pilot:

~~~powershell
python manage.py sync_mfforecast_holdings --scheme-code 100001 --scheme-code 100002 --scheme-code 100003 --dry-run
~~~

Replace those illustrative codes with the codes printed by `plan_mfforecast_pilot`.

Requests are throttled to no more than one per second per host. Transient network/server errors and HTTP 429 are retried with bounded backoff. HTTP 404 is recorded as no provider record; HTTP 401/403 stops the run. Raw JSON is cached under `backend/data/mf_raw/mfforecast_api/<retrieval-date>/`, which is git-ignored.

## Storage and date semantics

- Each response's metadata is preserved in `MFExternalPortfolioSnapshot`, including provider observation time, source URL, raw-cache path, scheme name, fund house, category, provider AUM, expense ratio, allocation and `provider_as_of_date`.
- Each holding is stored in `MFMasterHolding`, including ticker, ISIN, quantity, normalized NAV weight and normalized asset type. Identity matching uses the requested AMFI code; ISIN is retained at the security level.
- When the live response supplies `snapshot.snapshot_date`, PWMS stores that provider-supplied date as `provider_as_of_date` and uses it as the holding `portfolio_date` with the dated `DISCLOSURE` basis. This is a provider snapshot date, not independent verification that the AMC published a disclosure on that date. When no provider date exists, PWMS uses retrieval date and `FETCHED_AT`.
- Masterdata analytics prefers the latest dated holdings and only falls back to retrieval-date holdings when no dated holdings exist. If the selected dated rows came from MF Forecast, analytics adds `PORTFOLIO_DATE_FROM_PROVIDER_SNAPSHOT` to distinguish that provider-supplied date from an AMC-sourced disclosure date. Provider AUM and expense ratio remain in the external snapshot table; they are not copied into canonical `MFMasterData.aum_cr` or Direct/Regular TER fields.
- If the API's `holdings_count` differs from the number of parsed rows, PWMS adds `MFFORECAST_PROVIDER_HOLDINGS_COUNT_MISMATCH` and marks the source run `PARTIAL`. It does not infer weight units from a truncated response.
- Provider allocation is marked incomplete when any of equity/debt/cash is missing. Analytics uses the provider allocation only when all three values are valid; it does not infer a missing cash percentage from the other fields. The observed response has `cash_pct=null`, so its `32.99/67.01` figures are retained for audit but are not used to replace row-derived allocation metrics.

## Empty holdings responses

A successful HTTP response that produces zero valid holding rows is not evidence that a scheme has no investments. The provider array may be empty, or its field names/value formats may differ from the sample. PWMS retains the response metadata for audit, writes zero holding rows, adds the `MFFORECAST_NO_VALID_HOLDING_ROWS` quality flag, adds `MFFORECAST_INVALID_HOLDING_ROWS_SKIPPED` when individual items fail validation, and marks the source run `PARTIAL`. Check the raw JSON file path printed by the command and compare the result against the AMC's dated portfolio disclosure. Do not interpret a zero-holdings response as a zero-weight portfolio.

## Data rights and operating checks

Before scheduling repeated or bulk collection, confirm that MF Forecast's current terms/API plan permit the intended call volume, storage and downstream use. Public documentation alone does not establish redistribution rights. Start with a few schemes, compare their reported holdings and weights with the relevant AMC disclosure, and inspect the `MFExternalPortfolioSnapshot.quality_flags` and source-run record. Do not scale to all 14,000+ AMFI rows until provider limits and data rights are confirmed.
