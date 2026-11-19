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

## API

Authenticated endpoint:

```
GET /api/watch-list/analytics/india-market-feed/
```

The endpoint prefers the latest stored IndianAPI snapshot. If none exists, it falls back to the global AMFI master and latest AMFI NAV per scheme. The response identifies the source and keeps unavailable metrics null.

## Data limitations

IndianAPI's documented mutual-fund endpoint returns category-level point-in-time metrics. It is not a substitute for a complete historical NAV series, official benchmark total-return series, or dated portfolio holdings. PWMS continues to use AMFI for NAV history and must not infer stock-level attribution from aggregate fund returns.
