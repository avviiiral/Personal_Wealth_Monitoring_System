# India Mutual Fund Data Integration

PWMS uses a provider-independent approach for Indian mutual-fund research.

## Sources

- **AMFI (keyless baseline):** existing AMFIService downloads the official scheme/NAV master and historical NAV. This remains the primary source for scheme identifiers and NAV history.
- **IndianAPI (optional enrichment):** the GET /mutual_funds endpoint can supply category-level latest NAV, returns, asset size and star-rating fields. It requires an X-Api-Key header, including on the provider's free tier. PWMS calls it only from Django when INDIANAPI_API_KEY is configured.

IndianAPI documentation:
- https://indianapi.in/indian-stock-market
- https://indianapi.in/documentation/indian-stock-market

## Optional configuration

Add these values to backend/.env only when enabling IndianAPI:

```ini
INDIANAPI_API_KEY=your-key
INDIANAPI_BASE_URL=https://stock.indianapi.in
INDIANAPI_TIMEOUT_SECONDS=20
INDIANAPI_MONTHLY_REQUEST_BUDGET=400
```

Select `INDIANAPI_BASE_URL` to match the purchased plan: Free/Hobby uses `https://stock.indianapi.in`, Developer uses `https://dev.indianapi.in`, Growth Analyst uses `https://analyst.indianapi.in`, and Pro uses `https://pro.indianapi.in`. Keep development and production keys in their respective backend environment configurations; do not reuse a development key in production.

The default PWMS-side cap is 400 outbound requests per calendar month. The application dispatcher clamps any configured value to a hard maximum of 400. This is an application guardrail; it does not query or synchronize the provider's own quota dashboard.

The `/mutual_funds` endpoint returns the categorized mutual-fund universe in one bulk response. PWMS does not call the endpoint once per fund. The number of returned schemes therefore does not multiply the request count.

The key is server-side only. Never add it to Angular environment files, browser storage, source control or API responses.

Without the key:
- Django starts normally.
- Existing PWMS modules, AMFI sync, NAV history and the Nexedge research-report importer continue to work.
- The India fund-feed endpoint returns the locally stored AMFI scheme/NAV master when available.
- The IndianAPI sync command prints a skip message and exits successfully.
- Previously imported IndianAPI snapshots remain readable.

## Commands

Run from backend/:

```powershell
# Keyless baseline: official AMFI scheme master and latest NAVs
python manage.py refresh_amfi_master

# Optional enrichment: no-op when INDIANAPI_API_KEY is absent
python manage.py sync_indianapi_mutual_funds
```

The IndianAPI command stores a dated snapshot under report key indianapi-india; it does not overwrite the Nexedge report or investment transactions.

Every attempted outbound request is reserved in the database under its calendar month before network I/O. The ledger survives application restarts and is shared by all application workers using the same database. Timeouts and other connection failures count conservatively because the provider may have received the request. Once the configured budget is reached, PWMS blocks the request before contacting IndianAPI. No automatic retries are made.

## HTTP 429 / quota handling

IndianAPI documents HTTP 429 as either exhausted API credits or a plan rate limit. Check the account dashboard for current usage and remaining credits. Its current getting-started guide describes 500 credits as the initial Free-plan allowance; confirm whether your account has a monthly renewal before treating it as a recurring monthly quota. Plan allowances and rate limits can change. PWMS independently enforces the configured monthly budget. If the account was just renewed/upgraded and the error continues after a few minutes, contact IndianAPI support at contact@indianapi.in.

PWMS does not automatically retry a 429. The sync command reports provider details and any Retry-After / rate-limit headers. This avoids a tight retry loop and unnecessary requests. A failed refresh does not overwrite the last successfully stored IndianAPI snapshot; the keyless AMFI feed remains available.

Official provider guidance: https://indianapi.in/indian-stock-market

## API

Authenticated endpoint:

```
GET /api/mutual-fund-analytics/india-market-feed/
```

The endpoint prefers the latest stored IndianAPI snapshot. If none exists, it falls back to the global AMFI master and latest AMFI NAV per scheme. The response identifies the source and keeps unavailable metrics null.

## Data limitations

IndianAPI's documented mutual-fund endpoint returns category-level point-in-time metrics. It is not a substitute for a complete historical NAV series, official benchmark total-return series, or dated portfolio holdings. PWMS continues to use AMFI for NAV history and must not infer stock-level attribution from aggregate fund returns.
