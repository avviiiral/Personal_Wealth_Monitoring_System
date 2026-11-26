# Background jobs

PWMS runs its data refreshes inside the Django process. One small framework
(`backend/config/scheduling/`) owns every job; the portfolio news and exchange
filings loop keeps its own thread.

## Schedule

All times are Asia/Kolkata (`market.timezone`).

| Job name (`run_scheduled_job`) | At startup | Then | Work |
| --- | --- | --- | --- |
| `market_prices` | Fetches only assets that are not current | Every 15 minutes | Stocks, ETFs, SGBs, listed bonds, MIS reference tickers. Mutual funds are **not** in this loop. |
| `mf_nav` | Skipped if the latest published NAV is already stored | Nightly 00:00 | AMFI master refresh for owned ISINs (+ MIS history prefetch), latest NAV per fund into MarketPrice + holding rebuild, family NAV sync per active user |
| `sip` | After `mf_nav` | Right after a successful `mf_nav` | `sync_sip_installments`, then `execute_sips`, for each active user |
| `mf_intelligence` | Synced if no complete sync since the latest 01:00 slot | Nightly 01:00 | `MFIntelligenceSync().run(...)` (CharlieAlpha), shares `SYNC_LOCK` with the UI |
| `watchlist` | After `mf_intelligence` | Right after `mf_intelligence` | Benchmark history, `BackgroundWatchListRefreshService`, AMFI universe and performance |
| `security_master` | Run if the last success is more than 24 h old | Nightly 02:00 | Rebuild/merge `backend/data/security_master.xlsx` |
| `security_metrics` | Run if the last success is more than 24 h old | Nightly 02:30 | P/E, P/B, PEG, ROE, sector, market cap |
| `mf_underlying` | Run if the last success is more than 24 h old | Nightly 03:00 | `fetch_mf_underlying` |
| `corporate_actions` | Run if today's scan has not run | Nightly 03:30 | `monitor_corporate_actions --source nse` |
| (news loop) | Unchanged | Every 180 s | Portfolio news + exchange filings (`PortfolioNewsScheduler`) |

Two worker threads run the jobs:

* `pwms-jobs-interval` runs `market_prices`.
* `pwms-jobs-nightly` runs the nightly jobs **one after another** in the order
  above, does the startup catch-up, and retries failures.

## How "current" is decided

Every job checks whether its data is already current before it runs. A
restart therefore never re-runs a job that is current, and a slot that was
missed because the PC was off is caught up at the next startup.

* **`market_prices`**: per asset, using `MarketPrice.fetched_at` of the latest
  Yahoo (or SGB/bond) row:
  * during NSE market hours (Mon-Fri 09:15 to 15:30 plus `market.close_buffer_minutes`,
    default 15) an asset is never current: the latest stored day is
    re-fetched **inclusive**, so today's row keeps moving;
  * outside market hours an asset is current if its latest row was fetched
    after the most recent session close (+ buffer); otherwise it is fetched
    once more, which stores the official close. Weekends and holidays need no
    list; optional extra holidays go in `market.holidays` (`["2026-11-24"]`).
  * Rows written before this change have `fetched_at = NULL` and are refreshed once.
  * Assets are processed **stalest first** (never fetched, then oldest fetch).
* **`mf_nav`**: every owned fund with an ISIN has an AMFI NAV dated on or after
  the latest expected publish date (the last weekday on or before today after
  `publish_cutoff`, default 23:00; otherwise the last weekday before today).
  It also counts as current if the job succeeded after that date's cutoff
  (holidays, merged schemes). Funds without an ISIN are skipped with a reason.
* **`sip`, `watchlist`**: succeeded after their parent's latest slot (00:00 / 01:00).
* **`mf_intelligence`**: an `MFSyncRun` with status `success` or `partial`
  finished after the latest 01:00 slot and covered the scheduled scope (all
  configured categories, plans and steps, no `--limit`). A sync started from
  the UI (lock held, or a non-stale `running` row) makes the job skip and retry.
* **`security_master`, `security_metrics`, `mf_underlying`**: at startup,
  current if the last success is under `max_age_hours` (24) old. At the nightly
  slot, they run unless they already ran after that slot or less than
  `min_gap_hours` (12) ago, so a 23:00 catch-up does not repeat at 02:00 and the
  start time does not drift later every day.
* **`corporate_actions`**: current if today's scan succeeded.

Failed nightly jobs are retried after `scheduler.failure_retry_minutes` (60),
at most `scheduler.max_attempts_per_slot` (3) times per slot. `sip` runs only
after `mf_nav` succeeds or is current, because SIP execution creates
mutual-fund transactions.

## State file and logs

Each job's last attempt, last success, duration, counts and error are stored
in `backend/data/scheduler_state.json` (atomic, Windows-safe write; no
database table). Deleting the file makes every job "never run", so all of them
run at the next startup.

`backend/logs/jobs.log` has exactly one start line and one end line per job
run, with duration and counts, for example:

```
job market_prices started (reason=interval)
job market_prices finished status=success duration=6.2s assets=8 fetched=8 current=0 updated=6 skipped=2 failed=0 timed_out=0 records=6
job mf_nav current, not run (all 41 fund(s) have a NAV dated 2026-11-24 or later; reason=startup)
```

Per-asset detail is logged at DEBUG. Failures are summarised in one WARNING
line per run (first five names), not one line per asset. Exceptions are logged
with a traceback and never stop a worker thread.

## One provider can no longer stall the loop

* Each asset fetch has a hard wall-clock deadline
  (`per_asset_deadline_seconds`, default 45). A call that does not return in
  time is abandoned and the loop moves on; after `max_hung_provider_calls`
  (3) abandoned calls the cycle stops early and the next cycle retries.
* `BondPriceService` uses `(connect, read)` timeouts, downloads the CBRICS
  trade list once per cycle for all bonds, and after an HTTP 4xx stops calling
  NSE for `rejected_cooldown_hours` (24) with the status
  "NSE CBRICS rejected the bond price request (HTTP 400); bond prices were not
  changed; retry after ...". Set `providers.bond_price.enabled` to `false` to
  stop trying.
* AIFs, unlisted shares and similar holdings (transaction sub-class containing
  a keyword from `excluded_sub_class_keywords`) and bonds without an ISIN are
  left out of the loop instead of failing every run.
* A stock whose Yahoo symbol cannot be resolved is retried once per
  `unresolved_retry_hours` (24) instead of every 15 minutes.
* A symbol that resolves but keeps failing (stale or delisted on Yahoo, e.g.
  "No market data") is retried once per `failed_retry_hours` (6) after
  `failures_before_backoff` (3) failed passes in a row. A failure only counts
  while other prices are updating, in the same pass or since the symbol's
  streak began, so a Yahoo-wide outage never parks anything. One WARNING names
  the symbols when they are parked; the current list is in
  `backend/data/reports/price_failures.csv` (asset, symbol, ISIN, failures,
  last error, next retry). A success clears the entry. Set
  `failures_before_backoff` to `0` to retry every 15 minutes again.
* MIS reference tickers follow the same rule: one summary WARNING per refresh
  instead of one line per symbol, and a reference that fails three refreshes
  in a row is skipped until its retry, so it no longer forces a refresh every
  15 minutes after the close.
* An ambiguous Security Master name (several rows, different symbols) is
  warned about once per process, not on every cycle.

## Configuration keys (`backend/config/pwms_config.json`)

| Key | Default | Meaning |
| --- | --- | --- |
| `market.open`, `market.close` | `09:15`, `15:30` | NSE session |
| `market.close_buffer_minutes` | `15` | Minutes after the close still treated as intraday |
| `market.holidays` | `[]` | Optional `YYYY-MM-DD` dates treated as non-session days |
| `scheduler.startup_delay_seconds` | `10` | Delay before the first pass |
| `scheduler.failure_retry_minutes` | `60` | Retry delay for a failed nightly job |
| `scheduler.max_attempts_per_slot` | `3` | Failed attempts per slot before giving up until the next slot |
| `scheduler.jobs.market_prices.enabled` | `true` | |
| `scheduler.jobs.market_prices.interval_minutes` | `15` | |
| `scheduler.jobs.market_prices.per_asset_deadline_seconds` | `45` | Hard deadline per asset |
| `scheduler.jobs.market_prices.max_hung_provider_calls` | `3` | Abandoned calls before a cycle stops early |
| `scheduler.jobs.market_prices.unresolved_retry_hours` | `24` | Retry interval for unresolvable symbols |
| `scheduler.jobs.market_prices.failures_before_backoff` | `3` | Failed passes in a row (while other prices update) before a symbol is parked; `0` disables |
| `scheduler.jobs.market_prices.failed_retry_hours` | `6` | Retry interval for a parked symbol or MIS reference |
| `scheduler.jobs.market_prices.include_mis_reference_prices` | `true` | Refresh MIS reference tickers in the same loop |
| `scheduler.jobs.market_prices.excluded_sub_class_keywords` | AIF, UNLISTED, ... | Sub-classes with no exchange price |
| `scheduler.jobs.mf_nav.enabled` / `.time` / `.publish_cutoff` | `true` / `00:00` / `23:00` | |
| `scheduler.jobs.sip.enabled` | `true` | |
| `scheduler.jobs.watchlist.enabled` | `true` | |
| `scheduler.jobs.security_master.enabled` / `.time` / `.max_age_hours` / `.min_gap_hours` | `true` / `02:00` / `24` / `12` | |
| `scheduler.jobs.security_metrics.*` | `true` / `02:30` / `24` / `12` | |
| `scheduler.jobs.mf_underlying.*` | `true` / `03:00` / `24` / `12` | |
| `mf_intelligence.auto_sync_enabled` / `.auto_sync_time` | `true` / `01:00` | (previously `false` / `05:30`) |
| `mf_intelligence.auto_sync_plans` / `.auto_sync_categories` | `["Regular"]` / `[]` (all) | |
| `corporate_actions.daily_scheduler_enabled` / `.daily_run_time` | `true` / `03:30` | (previously 06:00) |
| `corporate_actions.nse_identity_matching` | `false` | See "Corporate actions" below |
| `providers.bond_price.enabled` | `true` | |
| `providers.bond_price.connect_timeout_seconds` / `.timeout_seconds` | `10` / `30` | Connect / read timeout |
| `providers.bond_price.response_cache_minutes` | `10` | One CBRICS download per cycle |
| `providers.bond_price.rejected_cooldown_hours` | `24` | Pause after HTTP 4xx |
| `security_resolver.queued_write_retry_seconds` | `300` | Retry interval for a queued workbook write |
| `security_resolver.conflict_log_sample_size` | `10` | Names shown in the conflict summary line |

Override any key per deployment with `PWMS_CONFIG_OVERRIDES` (JSON).

Environment variables: `PWMS_DATA_DIR` (runtime data directory, default
`backend/data`), `PWMS_LOGS_DIR` (default `backend/logs`),
`PWMS_START_SCHEDULERS` (see below).

## Which processes start the jobs

`config/scheduler_guard.py` decides:

| How you start the backend | Jobs start? |
| --- | --- |
| `python manage.py runserver` | Yes, once (in the autoreloader's worker process) |
| `python manage.py runserver --noreload` | **No** - there is no autoreloader, so `RUN_MAIN` is never set. Set `PWMS_START_SCHEDULERS=true` to start them in that single process. |
| `waitress-serve --listen=127.0.0.1:8000 config.wsgi:application` | Yes |
| `uvicorn config.asgi:application --host 127.0.0.1 --port 8000` | Yes |
| any other `manage.py` command, tests, pytest | No |
| any of the above with `PWMS_START_SCHEDULERS=false` | No |

Run **one** server process. `uvicorn --workers 4` or a multi-worker gunicorn
would start one copy of every job per process. On PostgreSQL each job takes an
advisory lock (`pwms_job_<name>`), so a second process skips a running job,
but on SQLite there is no cross-process lock.

Note: a plain Python script that calls `django.setup()` is "not manage.py",
so it starts the jobs too. Set `PWMS_START_SCHEDULERS=false` for such scripts.

## Running a job by hand

```bash
python manage.py run_scheduled_job --list               # schedule, last success, last status
python manage.py run_scheduled_job mf_nav               # run now, even if current
python manage.py run_scheduled_job security_master --check   # only report current/due and why
python manage.py run_scheduled_job market_prices --if-due    # apply the scheduler's rule
```

The result is recorded in the state file like a scheduled run. Existing
commands (`run_scheduled_refresh`, `update_market_prices`,
`sync_mf_intelligence`, `fetch_mf_underlying`, `monitor_corporate_actions`, ...)
still work for manual runs. `run_scheduled_refresh` is no longer started
automatically.

## Security Master (`backend/data/security_master.xlsx`)

For a stock or ETF price:

1. The workbook is consulted first: by ISIN, then by exact normalized name
   (`&` = `AND`, `LTD` = `LIMITED`, `(I)` = `INDIA`; punctuation, spacing and
   case ignored). A hit is used as is and **nothing is written**.
2. On a miss the existing order applies: hard-coded maps, explicit symbol, NSE
   symbol from `EQUITY_L.csv` (by ISIN, or exact company name for an asset
   without ISIN), then a Yahoo search accepted only when the result's
   normalized name equals the asset's.
3. The new mapping is saved to the workbook only after the first successful
   price download for it (that is the price-data validation). A name-only
   asset gets a row with a blank ISIN. The in-memory cache is updated directly;
   the workbook is not reloaded.
4. Every read and write holds one process-wide lock, and writes use a
   temporary file plus a Windows-safe replace with retries. If the file is
   locked (open in Excel), the mapping stays in memory, the write is queued
   and retried, and **one** warning is logged.
5. The workbook is loaded once per process and reloaded only when its
   modification time changes. A missing workbook is no longer generated
   during a price lookup; the nightly `security_master` job builds it.
6. Conflicting rows are quarantined. Each load logs one summary line (counts
   and the first 10 identifiers); the full list goes to
   `backend/data/reports/security_master_report.csv`.
7. Securities that cannot be resolved are listed in the same report
   (`needs_isin` when the asset has no ISIN, `unresolved` otherwise). Nothing
   is guessed.

**Correcting a wrong mapping.** Close PWMS or not, as you prefer: open the
workbook in Excel, fix the `Yahoo Symbol` in the row (or delete one of two
conflicting rows), save and close Excel. The next lookup sees the new
modification time and reloads. A mapping in the workbook always wins over a
newly resolved one; PWMS never overwrites an existing different symbol.

## Corporate actions

The nightly scan only records events, exposures, reconciliation states and
notifications for review. It never creates or changes a transaction; a
transaction is created only when you approve an item on the Corporate Actions
page.

NSE's corporate-actions API returns an ISIN per event, but the scan has been
ignoring it, and its fallback compares NSE's bare symbol (`HDFCBANK`) with the
stored Yahoo symbol (`HDFCBANK.NS`). So events only match a holding whose
asset name equals NSE's company name exactly. Run the read-only audit to see
this for your data:

```bash
python manage.py audit_corporate_action_matching
```

`corporate_actions.nse_identity_matching` (default `false`) enables the safe
matching rules: the provider's ISIN is used; otherwise the NSE symbol is
mapped to an ISIN only when the Security Master or `EQUITY_L.csv` gives exactly
one ISIN (and they agree); otherwise the existing explicit-approval flow
applies, now also finding assets stored as `SYMBOL.NS`. The method is stored in
`CorporateActionReconciliation.identity_match_method` (`SOURCE_ISIN`,
`SYMBOL_SECURITY_MASTER`, `SYMBOL_EQUITY_L`, `SYMBOL`, `NAME`). Turning it on
creates pending reconciliations and notifications for review; it does not
create transactions.

## Tests

Under `manage.py test` and pytest, log handlers write nothing to `logs/` and
`PWMS_DATA_DIR` points at a temporary directory, so tests never touch the real
log files, `security_master.xlsx` or `scheduler_state.json`.

## Database columns left by another branch

A database migrated from another branch can carry columns this branch's
models do not know. A NOT NULL column without a database default makes every
insert from this branch fail ("null value in column ... violates not-null
constraint"); the `mf_nav` job then fails on the AMFI master import.

```powershell
python manage.py check_schema_drift                 # read-only report
python manage.py check_schema_drift --app mutual_funds
python manage.py check_schema_drift --app mutual_funds --apply-safe-defaults
```

The report lists, per model table, extra columns (marked
`BREAKS INSERTS` when NOT NULL without a default, with the exact `ALTER TABLE`
it would run), model columns missing from the database, applied migrations
that do not exist on this branch, and unused tables. It writes
`backend/data/reports/schema_drift.csv`.

`--apply-safe-defaults` (PostgreSQL only) sets `''` on text columns, `false`
on booleans and `0` on numbers, in one transaction. It never drops a column,
never changes an existing row and never changes NULL/NOT NULL. Columns with no
safe default (dates, JSON) are reported and left for you. Check first that the
other branch does not read `false` on a flag such as `navall_active` as
"inactive" for the new rows this branch inserts. Undo with
`ALTER TABLE ... ALTER COLUMN ... DROP DEFAULT`.


## Mergers and demergers

Upload rows typed `*Merger`, `*Merged`, `*DeMerger` and `*DeMerger (New)` are corporate actions, not transactions.
The importer does not import them. Each group is stored as a pending review item (`PendingRestructuring`, migration
`investments/0112`), and the rows show in the upload's failed list as "Held for review (not imported)". Uploading the
same file again does not duplicate the item.

```
python manage.py review_restructurings                       # list, read-only
python manage.py review_restructurings --approve 5           # preview and the cost to confirm
python manage.py review_restructurings --approve 5 --cost 452000
python manage.py review_restructurings --approve 6 --cost 586300 --split "Allcargo Terminals=0.10,Transindia Real Estate=0.20"
python manage.py review_restructurings --reject 7
```

Approval creates only SELL and BUY transactions (source `OTHER`, dated on the event, noted with the review number):

* Merger: SELL the old stock at its own cost and BUY the new stock at that same amount. Realized gain is zero, net cash
  is zero, XIRR and P&L are unchanged, and the cost basis follows the shares.
* Demerger: the parent is sold and bought back at its reduced cost; each new company is bought at its share of the cost.
  The shares come from the company's cost-allocation notice, so `--split` is required and never guessed.

`--cost` must match the cost the holding carries today (cross-check against the existing holding engine). If the stock is
also held in another PMS or has later activity, the cost cannot be derived and you must pass it. `--force` skips the
cost/quantity cross-check. Original purchase dates are not carried over; the new lots are dated at the event.
