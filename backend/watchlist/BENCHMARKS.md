# BSE 500 benchmark

- Internal benchmark name: `BSE 500`
- Index identifier: `BSE500` (BSE 500 price index).
- Source: BSE / BSE Index Services historical index service, requested explicitly with `strIndex=BSE500`.
- The existing Nifty 50 path remains Yahoo Finance `^NSEI`.
- BSE 500 is fetched automatically in bounded annual windows and cached locally as `backend/watchlist/data/bse500.csv`.
- The scheduled refresh command updates the cache automatically; no daily `load_bse500` command is required.
- An optional `WATCHLIST_BENCHMARK_BSE500_URL` can override the BSE endpoint for a licensed/internal feed.
- BSE 500 is intentionally the price-return index; it is not BSE 500 TRI.

## Automatic update flow

1. `run_scheduled_refresh` calls `BenchmarkDataRefreshService.refresh_bse500()`.
2. The service reads the existing cache and fetches only the missing tail, with a 7-day overlap.
3. BSE historical requests use `ProduceCSVForDate/w` with `strIndex=BSE500`.
4. Responses are validated for date/value integrity and BSE500 identity before being cached.
5. Benchmark comparison continues to use the existing 1M/3M/6M/1Y/3Y/5Y calculation logic.

The benchmark can also read a verified local CSV cache, but normal operation does not require a manual loader.

## Historical range

The application only reports BSE 500 benchmark performance when validated BSE500 observations are available. It never substitutes BSE 500 price-return data or synthetic values.
