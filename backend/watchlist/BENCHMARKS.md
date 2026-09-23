# BSE 500 TRI benchmark

- Internal benchmark name: `BSE 500 TRI`
- Total-return identifier: `BSE500T` (BSE methodology's Bloomberg/Reuters TRI code).
- Source: BSE / BSE Index Services historical index service, requested explicitly with `strIndex=BSE500T`.
- The existing Nifty 50 path remains Yahoo Finance `^NSEI`.
- BSE 500 TRI is fetched automatically in bounded annual windows and cached locally as `backend/watchlist/data/bse500_tri.csv`.
- The scheduled refresh command updates the cache automatically; no daily `load_bse500_tri` command is required.
- An optional `WATCHLIST_BENCHMARK_BSE500_TRI_URL` can override the BSE endpoint for a licensed/internal feed.
- Do not substitute BSE 500 PRI. The loader rejects a BSE response whose returned index identity is not BSE500T / BSE 500 TRI.

## Automatic update flow

1. `run_scheduled_refresh` calls `BenchmarkDataRefreshService.refresh_bse500_tri()`.
2. The service reads the existing cache and fetches only the missing tail, with a 7-day overlap.
3. BSE historical requests use `ProduceCSVForDate/w` with `strIndex=BSE500T`.
4. Responses are validated for date/value integrity and BSE500T identity before being cached.
5. Benchmark comparison continues to use the existing 1M/3M/6M/1Y/3Y/5Y calculation logic.

The manual `load_bse500_tri` command remains available only for recovery/backfill from a verified CSV export.

## Historical range

The application only reports BSE 500 TRI benchmark performance when validated BSE500T observations are available. It never substitutes BSE 500 price-return data or synthetic values.
