# BSE 500 TRI benchmark

- Internal benchmark name: `BSE 500 TRI`
- Total-return identifier: `BSE500T` (BSE methodology's Bloomberg/Reuters TRI code).
- Source: BSE / BSE Index Services licensed BSE 500 Total Return Index.
- The existing Nifty 50 path remains Yahoo Finance `^NSEI`.
- BSE 500 TRI is read from a validated source CSV, or from the optional `WATCHLIST_BENCHMARK_BSE500_TRI_URL`.
- Set `WATCHLIST_BENCHMARK_BSE500_TRI_FILE` when the canonical CSV is stored outside the repository.
- Do not substitute BSE 500 PRI. The public `IndexArchDailyAll` endpoint is not used for TRI because it is the price-index archive.

## Load/update

Export the BSE 500 TRI history from the licensed BSE source and run:

    python manage.py load_bse500_tri --file <path-to-bse500-tri.csv>

For deployments that keep the data file outside the repository, set `WATCHLIST_BENCHMARK_BSE500_TRI_FILE` to that path. For an approved internal HTTP mirror of the licensed export, set `WATCHLIST_BENCHMARK_BSE500_TRI_URL`.

The normal benchmark calculation logic is unchanged: the service selects the latest valid observation at or before each period cutoff, computes 1M/3M/6M/1Y/3Y/5Y returns, and returns the existing unavailable response when required history is absent.

## Historical range

The application does not claim a loaded historical range until a real BSE 500 TRI export is installed. This avoids silently reporting fabricated or price-index data as TRI.