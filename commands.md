# PWMS Command Reference

This catalogue describes the project-defined Django management commands and the standard development, database, and frontend commands used by PWMS. It is based on the command implementations on `feat/mf-intelligence-db-integration`.

**Execution context:** Unless stated otherwise, run Django commands from `backend/` with the project's virtual environment active:

```powershell
cd D:\PWMS\Personal_Wealth_Monitoring_System\backend
.\venv\Scripts\Activate.ps1
python manage.py <command> --help
```

The command inventory below covers the **47 PWMS-defined management commands**. Django and Django REST Framework also provide built-in commands; these are listed separately and are not counted as project-defined commands.

## Safety classifications

- **Read-only / diagnostic:** intended to inspect data without changing it.
- **Dry-run by default:** prints proposed changes; a write flag such as `--apply` is required.
- **Writes data:** imports, refreshes or recalculates database records.
- **Destructive:** deletes records or replaces a data set. Read the warning before execution.

A command's repeatability does not mean that its external data source is authoritative or guaranteed to be available. Review the command's output and the relevant feature runbook before running bulk operations.

## 1. Mutual Fund Intelligence

These commands populate the shared, database-backed MF Intelligence reference dataset. They are separate from the legacy AMFI family NAV commands in the `mutual_funds` app.

| Command | What it does | Options |
| --- | --- | --- |
| `sync_mf_intelligence` | Fetches the category catalogue and scheme master, category-level metrics/medians, scheme NAV history, portfolio disclosures, AUM history, benchmark history and AMFI market-cap classification; stores the results in PWMS database tables and records a `MFSyncRun`. Network fetches use a bounded worker pool and shared CharlieAlpha rate limiter; database writes are serialized through the database scheduler lock. | `--category SLUG` (repeatable); `--plan Regular\|Direct\|Both` (default `Regular`); `--force`; `--limit N`; `--workers N`; `--skip-nav`; `--skip-holdings`; `--skip-aum`; `--skip-benchmarks`; `--skip-cap-list`. |
| `import_mf_cap_classification` | Imports AMFI's Large/Mid/Small-cap company list into MF Intelligence. Without `--file`, it tries to download the newest available half-year workbook. | `--file PATH` to import a local workbook; `--force` to download again even when the latest period is already stored. |

Examples:

```powershell
python manage.py sync_mf_intelligence --help
python manage.py sync_mf_intelligence --category multi-cap --limit 20
python manage.py sync_mf_intelligence --plan Both
python manage.py sync_mf_intelligence --force
python manage.py sync_mf_intelligence --skip-nav --skip-holdings
python manage.py import_mf_cap_classification
python manage.py import_mf_cap_classification --file .\data\AverageMarketCapitalization30Jun2026.xlsx
```

**Operational notes**

- `--category` may be repeated. Omitting it selects all categories in the provider catalogue.
- `--plan Both` selects Regular and Direct plan scheme targets for NAV, holdings and AUM sync; it is not limited to NAV.
- `--limit` bounds the number of target schemes and is useful for a controlled test run.
- `--force` bypasses freshness checks for per-scheme NAV, holdings and AUM data and forces benchmark/cap-list refresh work.
- The command's category catalogue and category metrics are refreshed as part of the run even when individual fund-data steps are skipped.
- A sync is protected by a process-local `SYNC_LOCK`; another MF Intelligence sync in the same process is rejected. The database scheduler lock serializes writes, including on SQLite.
- The nightly `mf_intelligence` background job runs this sync at `mf_intelligence.auto_sync_time` (default `01:00`, Asia/Kolkata) when `auto_sync_enabled` is true (now the default), with `auto_sync_plans` and optional `auto_sync_categories`. At startup it syncs only if no complete sync finished after the latest 01:00 slot. It never starts while a sync started from the UI is running. Run it by hand with `python manage.py run_scheduled_job mf_intelligence`.

See [MF Intelligence implementation notes](./docs/MF_INTELLIGENCE.md).

## 2. Market data and Security Master

| Command | What it does | Options / behavior |
| --- | --- | --- |
| `update_market_prices` | Refreshes prices for the market-price asset scope (STOCK, ETF and bond/SGB assets, plus eligible legacy Direct Equity records). Mutual funds are excluded, including funds an older import stored as BOND/STOCK. It can refresh the Security Master workbook when held STOCK/ETF ISINs are missing from its mapping, then resolves symbols and saves price history. | Optional `--user-id ID` limits assets to one owner. Uses the PostgreSQL advisory lock `pwms_refresh_pipeline`. |
| `refetch_recent_prices` | **One-off repair.** Re-downloads the last N days of Yahoo prices for every stock/ETF in the price loop and overwrites those rows, replacing intraday values stored before the freshness fix with official closes. SGBs and bonds are not touched. | `--days N` (default 30); `--dry-run` lists assets only. Uses `pwms_refresh_pipeline`. Review a dry run first. |
| `diagnose_security_resolver` | Read-only diagnostic of how eligible assets resolve to Yahoo symbols through the SecurityResolver and Security Master. Does not write Asset/MarketPrice/workbook data and makes no Yahoo/NSE network calls. | Repeatable `--asset-id ID`; repeatable `--isin ISIN`; `--user-id ID`; `--only-issues`; `--format table\|json`. Explicit IDs/ISINs bypass the default diagnostic scope. |
| `refresh_security_metrics` | Refreshes stock/ETF P/E, P/B, PEG, sector and market-cap metrics from Yahoo Finance using the market-data service. | No command-line options. |
| `refresh_security_master` | Fetches Yahoo Finance sector, P/E, P/B, PEG and ROE for resolvable SecurityMaster rows. It is **dry-run by default**; `--apply` saves changes. | `--apply`; `--limit N`. The command overwrites these live-tracked fields on successful apply; it does not update AMC, credit rating, YTM, duration, average maturity or cap type. |
| `fetch_market_data` | Fetches Yahoo Finance historical price data and stores it for a PWMS Asset. Both a Yahoo symbol and an existing Asset ID are required. | Required `--symbol SYMBOL`; required `--asset-id ID`; optional `--period` (default `1y`, e.g. `1mo`, `3mo`, `6mo`, `1y`, `5y`). |
| `refresh_market_data` | Fetches and rebuilds market data for all active STOCK and ETF assets, using a one-year period. | No command-line options. This is a separate helper from `update_market_prices`; review the output scope before use. |
| `refresh_mis_reference_prices` | Refreshes Yahoo Finance reference prices used by the MIS Notes sheet. | No command-line options. |
| `backfill_price_history` | Backfills historical STOCK/ETF prices from Yahoo Finance and mutual-fund NAV from AMFI, starting at each asset's earliest transaction date. Existing MarketPrice rows are updated rather than duplicated. BOND history is not supported by this command. | Optional `--user-id ID`; omit to cover all users. |
| `discover_security_identities` | Discovers security identifiers using official exchange/depository evidence. | `--since YYYY-MM-DD` (default `2000-01-01`); `--until YYYY-MM-DD` (default today); `--exchange NSE\|BSE\|NSDL\|CDSL`; `--asset-id ID`; `--dry-run`; `--force-refresh`. |
| `link_security_master` | Links an Asset to an existing SecurityMaster row only when owner and ISIN match. Does not invent or populate SecurityMaster fields. **Dry-run by default.** | `--apply`; optional `--user-id ID`. |
| `load_security_master_data` | Loads researched SecurityMaster values from `investments/data/security_master_lookups.json`, matched by ISIN. **Dry-run by default** and protects populated values unless overwrite is requested. | `--apply`; `--overwrite`. |
| `import_amfi_cap_classification` | Classifies SecurityMaster rows from a supplied AMFI average-market-capitalisation workbook using ISIN and rank. **Dry-run by default.** | Required `--file PATH`; optional `--user-id ID`; `--apply`; `--overwrite` to replace existing cap classifications. |
| `refresh_underlying_metadata` | Re-resolves ISIN, sector and cap classification for uploaded PMS/asset underlying-holding rows and saves changed metadata. | No command-line options. |
| `repair_asset_identity` | Rebuilds Excel-imported Asset identities by deleting the owner's Excel-sourced investment transactions, removing now-orphaned affected assets/positions, re-importing the synchronized transaction workbook and rebuilding positions. Manual-source transactions and the separate mutual-fund pipeline are not targeted. | Optional `--user-id ID`; omit to process all users with Excel-sourced transactions. **Destructive; there is no dry-run flag.** Confirm the synchronized workbook exists and is correct before running. If it is missing, the command can leave the deleted Excel transactions unre-imported. |

Examples:

```powershell
python manage.py diagnose_security_resolver --only-issues
python manage.py diagnose_security_resolver --isin INE009A01021 --format json
python manage.py update_market_prices --user-id 1
python manage.py fetch_market_data --symbol RELIANCE.NS --asset-id 123 --period 5y
python manage.py link_security_master
python manage.py link_security_master --apply
python manage.py refresh_security_master --limit 10
python manage.py refresh_security_master --limit 10 --apply
python manage.py backfill_price_history --user-id 1
```

See [Security Resolver Diagnostics](./docs/operations/security-resolver-diagnostics.md) for the resolution statuses and safe investigation workflow.

## 3. Portfolio and transaction maintenance

| Command | What it does | Options / behavior |
| --- | --- | --- |
| `import_transactions` | Imports an Excel/CSV transaction workbook through the same TransactionImporter used by the application upload API. Uses source-key / field-based duplicate checks so repeat imports skip existing rows. | Optional `--file PATH`; exactly one required owner selector: `--username NAME`, `--user-id ID` or `--all-users`. |
| `rebuild_holdings` | Rebuilds portfolio holdings from investment transactions. | Optional `--user-id ID`; optional `--asset-id ID`. |
| `audit_portfolio_xirr` | Reports the exact transaction cash flows and terminal valuation used by Portfolio XIRR for a subclass or asset-name/strategy summary. | Required `--family-id ID` and `--sub-class NAME`; optional `--asset-name`, `--family-name`, `--asset-class`, `--advisor`. |
| `repair_asset_identity` | Repairs imported Asset identity collisions by re-importing Excel-sourced transactions; see the destructive warning in the Security Master section. | Optional `--user-id ID`. |

Examples:

```powershell
python manage.py import_transactions --username <username> --file .\data\transactions.xlsx
python manage.py import_transactions --user-id 1
python manage.py rebuild_holdings --user-id 1
python manage.py rebuild_holdings --user-id 1 --asset-id 123
python manage.py audit_portfolio_xirr --family-id 1 --sub-class "Direct Equity"
```

## 4. Mutual funds, AMFI and SIPs

| Command | What it does | Options / behavior |
| --- | --- | --- |
| `refresh_amfi_master` | Refreshes the shared global AMFI scheme and NAV master. | No command-line options. |
| `fetch_amfi_nav` | Refreshes the shared AMFI NAV master. An optional legacy user ID also materializes the latest master data into that user's family. Can request historical NAV dates. | Optional `--user-id ID`; `--from-date YYYY-MM-DD`; `--to-date YYYY-MM-DD`. |
| `refresh_investment_amfi` | Refreshes latest AMFI NAVs for investment ISINs and backfills missing MIS history. | Repeatable `--isin ISIN`; omit to use the command's normal investment scope. |
| `import_mf_nav` | Imports historical NAV data for one mutual-fund scheme code. | Required `--scheme-code CODE`; `--from-date YYYY-MM-DD` (source default `2026-01-01`); `--to-date YYYY-MM-DD` (source default `2026-08-07`). Supply explicit dates for the intended historical window. |
| `resolve_amfi_scheme` | Resolves an AMFI scheme code to its historical NAV detail ID. | One required positional argument: `scheme_code`. |
| `fetch_mf_underlying` | Fetches the latest official AMFI/AMC mutual-fund portfolio disclosures. | Optional `--scheme-id ID`; optional `--owner-id ID`. |
| `rebuild_mf_holdings` | Rebuilds mutual-fund holdings from mutual-fund transactions. | Optional `--user-id ID`; optional `--scheme-id ID`. |
| `recalculate_mf_transactions` | Recalculates mutual-fund transaction NAV and units using historical NAV data for a scheme. | Required `--scheme-code CODE`. |
| `sync_sip_installments` | Generates, synchronizes and reconciles SIP installments for one user. | Required `--user-id ID`. |
| `execute_sips` | Executes due SIP installments for one user. | Required `--user-id ID`. |

Examples:

```powershell
python manage.py refresh_amfi_master
python manage.py fetch_amfi_nav --from-date 2025-01-01 --to-date 2026-11-25
python manage.py refresh_investment_amfi --isin INF123456789
python manage.py fetch_mf_underlying --scheme-id 123
python manage.py rebuild_mf_holdings --user-id 1
python manage.py sync_sip_installments --user-id 1
python manage.py execute_sips --user-id 1
```

**Financial-data caution:** NAV/transaction recalculation and SIP execution can affect downstream holdings and valuation. Check the relevant user/scheme and historical coverage before running maintenance commands.

## 5. Watch List

| Command | What it does | Options / behavior |
| --- | --- | --- |
| `refresh_watchlist` | Refreshes the generic Watch List investment research universe. | No command-line options. |
| `prepare_watchlist_benchmark_chart` | Prepares shared AMFI NAV history for watchlisted mutual-fund products and shared benchmark master history required by Watch List comparison charts. | Optional `--product-id ID`; `--period` accepts the choices defined by BenchmarkPerformanceService (default `5Y`). |

Examples:

```powershell
python manage.py refresh_watchlist
python manage.py prepare_watchlist_benchmark_chart
python manage.py prepare_watchlist_benchmark_chart --product-id 123 --period 5Y
```

## 6. Corporate actions, filings and Portfolio News

| Command | What it does | Options / behavior |
| --- | --- | --- |
| `monitor_corporate_actions` | Ingests structured corporate-action events and matches them to portfolio exposures. Sources can be NSE, a configured feed, stored exchange filings or both feed and filings. | `--source nse\|feed\|filings\|both` (default `nse`); `--hours N`; or `--since YYYY-MM-DD` and `--until YYYY-MM-DD`; `--dry-run`; `--report-json PATH` (only with `--dry-run`). Date range and `--hours` are mutually exclusive. |
| `ingest_exchange_filings` | Ingests NSE/BSE exchange filings and generates portfolio/watchlist alerts. | Repeatable `--exchange nse\|bse`; `--hours N` (default `24`); `--dry-run`. |
| `diagnose_corporate_filings` | Read-only diagnostics of stored filings against active STOCK/ETF assets, holdings, Watch List entries and existing Portfolio News alerts. | `--hours N` (default `24`); repeatable `--exchange nse\|bse`; `--limit N` (default `10`, unmatched examples). |
| `monitor_portfolio_news` | Runs one news-monitor pass for active users' holdings, applies the configured rule-based relevance/impact pipeline and creates portfolio-weighted alerts. | `--loop` runs continuously at `NEWS_MONITOR_INTERVAL` cadence until stopped. |
| `generate_web_push_keys` | Generates a VAPID key pair for browser Web Push. Store the private key securely in backend configuration; do not commit it. | No command-line options. |
| `gemini_usage` | Summarizes Gemini API call counts and prompt/output/cached/total token usage, grouped by endpoint, over the requested lookback window. | `--days N` (default `30`). |

Examples:

```powershell
python manage.py diagnose_corporate_filings --hours 48 --exchange nse
python manage.py ingest_exchange_filings --exchange nse --hours 24 --dry-run
python manage.py monitor_corporate_actions --source nse --dry-run
python manage.py monitor_corporate_actions --source filings --since 2026-11-01 --until 2026-11-25
python manage.py monitor_portfolio_news
python manage.py gemini_usage --days 30
python manage.py generate_web_push_keys
```

The corporate-action `--report-json` option is intentionally restricted to dry-run so that audit generation is read-only. Feed-based ingestion requires an authorised feed URL in configuration.

## 7. Scheduled refresh orchestration

### `run_scheduled_job`

Runs one background job on demand (the same code the in-process scheduler runs) and records the result in `backend/data/scheduler_state.json`.

```powershell
python manage.py run_scheduled_job --list                    # every job, schedule, last success / status
python manage.py run_scheduled_job mf_nav                    # run now, even if current
python manage.py run_scheduled_job security_master --check   # report current/due and why; run nothing
python manage.py run_scheduled_job market_prices --if-due    # run only if not current
```

Job names: `market_prices`, `mf_nav`, `sip`, `mf_intelligence`, `watchlist`, `security_master`, `security_metrics`, `mf_underlying`, `corporate_actions`. Schedule and rules: [BACKGROUND_JOBS.md](./docs/operations/BACKGROUND_JOBS.md).

### `run_scheduled_refresh`

Runs the project's end-to-end operational refresh pipeline for a manual run. It is no longer started automatically (the background jobs above replace the old daily refresh thread). It is guarded by the PostgreSQL advisory lock `pwms_refresh_pipeline` and continues to later steps when an individual step fails, reporting failures at the end.

The current sequence includes:

1. `update_market_prices` (global).
2. `refresh_mis_reference_prices` (global).
3. Synchronize owned AMFI NAVs from the shared master for active users.
4. `sync_sip_installments` then `execute_sips` for each active user.
5. Refresh the Watch List universe and Watch List performance.
6. `refresh_security_master --apply`.
7. `monitor_portfolio_news`.
8. `monitor_corporate_actions`.

Option: repeat `--skip COMMAND_NAME` to omit an individual named step. For example:

```powershell
python manage.py run_scheduled_refresh --skip monitor_portfolio_news --skip monitor_corporate_actions
```

The current implementation does **not** invoke `sync_mf_intelligence`; use `run_scheduled_job mf_intelligence` for that.

### Background jobs

All background data jobs run in one framework started by the Django process (`config/scheduling/`), subject to `config.scheduler_guard`: `runserver` (worker process), `waitress` and `uvicorn` start them; `runserver --noreload` does not unless `PWMS_START_SCHEDULERS=true`. See [BACKGROUND_JOBS.md](./docs/operations/BACKGROUND_JOBS.md) and [Windows scheduler instructions](./scripts/windows/README.md) before configuring an additional external schedule.

### Read-only reports added with the background jobs

| Command | What it does |
| --- | --- |
| `report_fund_categories` | Lists mutual funds stored as BOND/STOCK/CASH that migration `investments.0111` would change to MUTUAL_FUND, and the ones it would leave alone (possible ETFs, duplicate ISINs). `--csv` also writes `data/reports/fund_category_repair.csv`. After 0111 has run, `--already-applied` lists the assets it most likely changed (inferred from the old importer rule and the asset creation date; 0111 kept no record); with `--csv` it writes `data/reports/fund_category_repair_applied.csv`. Changes nothing. |
| `check_schema_drift` | Compares the database with this branch's models: extra columns (flags NOT NULL columns without a default that break inserts), missing columns, applied migrations not on this branch, unused tables. `--app <label>` limits it. Writes `data/reports/schema_drift.csv`. Read-only unless `--apply-safe-defaults` (PostgreSQL), which only sets column defaults (`''`, `false`, `0`); see [BACKGROUND_JOBS.md](./docs/operations/BACKGROUND_JOBS.md#database-columns-left-by-another-branch). |
| `review_restructurings` | Lists mergers/demergers held from transaction uploads (`*Merger`, `*Merged`, `*DeMerger`, `*DeMerger (New)`). Listing is read-only. `--approve ID --cost AMOUNT [--split "New Co=0.10,..."] [--force]` creates the SELL/BUY transactions that carry cost to the new holding (no realized gain, net cash zero); `--reject ID` creates nothing. Nothing is ever created without `--approve`. See [BACKGROUND_JOBS.md](./docs/operations/BACKGROUND_JOBS.md#mergers-and-demergers). |
| `audit_corporate_action_matching` | Fetches NSE corporate actions for held stocks (like the scan) and reports how many match holdings now, why the rest do not, and how many would match with `corporate_actions.nse_identity_matching=true`. Writes `data/reports/corporate_action_matching.csv`; changes no database row. Needs access to nseindia.com. |

## 8. Database reset commands — destructive

The PWMS `users` app overrides Django's built-in `flush`. These commands preserve schema and Django migration history but permanently delete data. **Do not run them against a valuable database without a verified backup and an independently reviewed dry-run.**

| Command | Scope |
| --- | --- |
| `flush` | Clears all physical application tables, including unmanaged/legacy tables; preserves schema and `django_migrations`. |
| `safe_flush1` | Clears non-account application data while preserving users, groups/permissions/content types, profiles, families, memberships, preferences and user audit logs. Sessions and some other account-adjacent records are not preserved. |
| `safe_flush2` | Deletes investment and mutual-fund transaction ledgers through Django ORM deletion; transaction edit history cascades and SIP transaction links are set to NULL. Assets, holdings, portfolio positions, SIP instructions/installments and reference data are not directly deleted. |

For `flush` and `safe_flush1`, supported options include `--dry-run`, `--database ALIAS`, `--noinput`, `--reset-sequences`, `--inhibit-post-migrate` and `--allow-cascade` (the last is deliberately rejected). For `safe_flush2`, options are `--dry-run`, `--database ALIAS` and `--noinput`.

Always start with:

```powershell
python manage.py flush --dry-run
python manage.py safe_flush1 --dry-run
python manage.py safe_flush2 --dry-run
```

Read [Database Reset Commands](./docs/operations/database-reset-commands.md) for the exact preservation scope, confirmation behavior and database-engine implementation. Dry-run output is not a backup.

**Special warning:** `repair_asset_identity` is not a database reset command, but it deletes Excel-sourced investment transaction rows before re-import. It has no dry-run option and must be treated as destructive.

## 9. Other useful Django commands

These are provided by Django (or installed Django packages), not custom PWMS command modules.

| Command | Purpose |
| --- | --- |
| `python manage.py check` | Run Django system checks. |
| `python manage.py migrate` | Apply database migrations. |
| `python manage.py makemigrations --check --dry-run` | Check for model changes without creating migration files. |
| `python manage.py showmigrations` | Show migration application status. |
| `python manage.py createsuperuser` | Create an administrative login. |
| `python manage.py runserver` | Start the Django development server (not a production server). |
| `python manage.py test` | Run Django tests. |
| `python manage.py shell` | Open a Django-aware Python shell. |
| `python manage.py collectstatic` | Collect static assets for deployment. |
| `python manage.py dumpdata` / `loaddata` | Export/import Django fixtures; not a substitute for a database backup/restore. |
| `python manage.py sqlflush` | Display SQL generated for the custom flush implementation; inspect only, do not paste SQL into PowerShell. |

The command list available in the active environment can be inspected with:

```powershell
python manage.py help
```

## 10. Frontend and repository commands

Run these from `frontend/\` unless otherwise noted.

| Command | Purpose |
| --- | --- |
| `npm ci` | Install the exact dependency tree from `package-lock.json`. |
| `npm start` | Start Angular's development server (port 4200; proxies `/api` to Django). |
| `npm run build` | Create the production browser build under `dist/frontend/browser/`. |
| `npm run test -- --watch=false` | Run Angular/Vitest unit tests once. |
| `npm audit --audit-level=high` | Audit frontend dependencies for high-severity vulnerabilities. |

Backend verification from `backend/\`:

```powershell
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py test
pip-audit -r requirements.txt
```

Windows Task Scheduler registration is run from the repository root:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\windows\register_market_price_scheduler.ps1
Get-ScheduledTaskInfo -TaskName "PWMS-Market-Price-Refresh"
Unregister-ScheduledTask -TaskName "PWMS-Market-Price-Refresh" -Confirm:$false
```

The scheduled task runs `update_market_prices` hourly. It does not run the complete daily `run_scheduled_refresh` pipeline.

## 11. How to verify command details

Command options can change with code. The source of truth is each module under `backend/<app>/management/commands/`. Before running an unfamiliar or high-impact command:

1. Run `python manage.py <command> --help`.
2. Read the command module and any service it calls.
3. Check the selected database and user/scheme scope.
4. Use a dry-run where the command provides one.
5. Inspect logs and result counts after execution.

This document describes the command implementations on the branch named at the top. Recheck it when command modules change.
