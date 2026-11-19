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
INDIANAPI_MONTHLY_REQUEST_BUDGET=450
```

The development default is a PWMS-side cap of 450 outbound requests per calendar month, reserving 50 credits against a 500-credit allowance. For production, configure this value to the purchased plan's monthly allowance minus a deliberate safety reserve. This is an application guardrail; it does not query or synchronize the provider's own quota dashboard.

The `/mutual_funds` endpoint returns the categorized mutual-fund universe in one bulk response. PWMS does not call the endpoint once per fund. The number of returned schemes therefore does not multiply the request count.
```

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

## HTTP 429 / quota handling

IndianAPI documents HTTP 429 as either exhausted API credits or a plan rate limit. Check the account dashboard for current usage and remaining credits. The Free plan's published starting allowance is 500 API credits; plan allowances and rate limits can change. If the account was just renewed/upgraded and the error continues after a few minutes, contact IndianAPI support at contact@indianapi.in.

PWMS does not automatically retry a 429. The sync command reports provider details and any Retry-After / rate-limit headers. This avoids a tight retry loop and unnecessary requests. A failed refresh does not overwrite the last successfully stored IndianAPI snapshot; the keyless AMFI feed remains available.

Official provider guidance: https://indianapi.in/indian-stock-market

## API

Authenticated endpoint:

```
GET /api/watch-list/analytics/india-market-feed/
```

The endpoint prefers the latest stored IndianAPI snapshot. If none exists, it falls back to the global AMFI master and latest AMFI NAV per scheme. The response identifies the source and keeps unavailable metrics null.

## Data limitations

IndianAPI's documented mutual-fund endpoint returns category-level point-in-time metrics. It is not a substitute for a complete historical NAV series, official benchmark total-return series, or dated portfolio holdings. PWMS continues to use AMFI for NAV history and must not infer stock-level attribution from aggregate fund returns.
