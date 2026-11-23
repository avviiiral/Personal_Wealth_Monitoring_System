# Mutual Fund Masterdata — Automated Ingestion Pipeline

This pipeline keeps `mf_masterdata` filled for every Indian mutual-fund scheme: scheme master, latest NAV, AUM, TER, AMFI-published risk figures, PWMS-calculated 1Y/3Y ratios and portfolio holdings. It **starts automatically with the backend** and then refreshes itself on an IST timeline.

It extends the existing masterdata work (see [operations/mf_masterdata.md](operations/mf_masterdata.md)) and reuses its tables. It does not touch family-scoped holdings, transactions, XIRR, portfolio valuation, API permissions or family-level isolation. Only AMC names of held schemes are read across families, to decide which AMC disclosures to import.

Code: `backend/mutual_funds/services/mf_pipeline/` (jobs, parsers, scheduler) and `backend/mutual_funds/services/mf_ratios.py` (ratio maths).

## How it runs

When the backend starts (`manage.py runserver`, waitress or uvicorn — never for one-off management commands or tests), `MutualFundsConfig.ready()` starts `MFPipelineScheduler`:

1. It waits `PWMS_MF_PIPELINE_STARTUP_DELAY_SECONDS` (default 60) so the server finishes booting.
2. It closes runs left `RUNNING` by a previous, stopped backend.
3. **Startup run:** it runs every job once in dependency order: NAV → backfill → gap check → TER → AUM → risk → risk-free → benchmark → holdings → calculated ratios. A job is skipped only if it already ran within its cooldown (e.g. NAV 2h, TER 1 day), so code reloads and restarts don't hammer AMFI. The NAV backfill runs on startup only until it has succeeded once.
4. **Timeline:** after that, it polls every 30 s and runs jobs at their scheduled IST slots.

Every job is idempotent and exits early with status `SKIPPED` when its source is unchanged (content hash), the period is already loaded, the data isn't published yet, or the source isn't configured. Retry slots therefore just re-run the job.

With PostgreSQL, a per-job advisory lock prevents duplicate runs across worker processes. Database writes use the shared `DATABASE_SCHEDULER_LOCK`, so the pipeline doesn't collide with PWMS's other schedulers on SQLite.

| Setting (env) | Default | Meaning |
|---|---|---|
| `PWMS_MF_PIPELINE_ENABLED` | `1` | `0` disables the scheduler |
| `PWMS_MF_PIPELINE_RUN_ON_STARTUP` | `1` | `0` = timeline only, no startup run |
| `PWMS_MF_PIPELINE_STARTUP_DELAY_SECONDS` | `60` | Delay before the startup run |
| `PWMS_MF_CALC_SCOPE` | `growth` | Schemes rebuilt daily: `growth` (active Growth plans), `held`, `all` |
| `PWMS_MF_BACKFILL_SCOPE` | `held` | Schemes in the automatic mfapi.in backfill |
| `PWMS_MF_GAP_CHECK_MAX_REQUESTS` | `300` | mfapi.in request cap per gap-check run |
| `PWMS_MFAPI_MIN_INTERVAL_SECONDS` | `1.0` | Minimum spacing between mfapi.in requests |
| `PWMS_MF_HOST_MIN_INTERVAL_SECONDS` | `1.0` | Minimum spacing for other hosts (AMFI) |
| `PWMS_MF_RISK_CATEGORY_IDS` | `17,18` | AMFI risk-parameter categories |
| `PWMS_MF_RISK_FREE_URL` | empty | Optional CSV/XLSX URL with date + T-bill yield |
| `PWMS_MF_USER_AGENT` | PWMS UA | User-Agent sent to every source |

The first startup on an empty database is the heaviest. The NAV load takes seconds, TER about a minute, and AUM/risk seconds. The gap check makes up to 300 mfapi.in calls (about 5 minutes), and the first full ratio build for ~7,000 Growth schemes takes roughly 45–60 minutes in the background. Later days are incremental.

## Schedule (IST)

| Job (`manage.py …`) | Source | Schedule | Retry |
|---|---|---|---|
| `mf_sync_nav` | AMFI NAVAll.txt | Mon–Sat 23:30 | 09:30 and 11:00 next day (late FoF/overseas NAVs) |
| `mf_backfill_nav` | mfapi.in (held schemes) / captn3m0 archive | Startup until first success; otherwise manual | — |
| `mf_nav_gap_check` | mfapi.in | Sunday 03:00 | — |
| `mf_sync_benchmark` | Operator files / configured URLs | Mon–Fri 20:00 | 09:00 next morning |
| `mf_sync_risk_free` | Operator files / configured URL / manual | Thursday 10:00 | Daily 10:00 (no-op until a new file appears) |
| `mf_sync_ter` | AMFI TER export via amfipy | Monday 06:00 | — |
| `mf_sync_risk` (+ tracking error) | AMFI via amfipy | 10th 06:00 | Daily 06:00 until loaded, up to the 20th |
| `mf_sync_aum` | AMFI scheme-wise AAUM via amfipy | 10th 06:00 | Daily 06:00 until the new period appears, up to the 20th |
| `mf_sync_holdings` | AMC disclosures (files / configured URLs) | 11th 05:00 | Daily to the 15th; 16th–20th also picks up the 15th (debt fortnightly) |
| `mf_calc_ratios` | Internal | Daily 00:30 | — (skips if already done for the NAV date) |

Trading days are Mon–Fri. No exchange-holiday calendar is applied; a holiday run is a harmless no-op.

## Running jobs manually

Run from `backend/` with the virtualenv active:

~~~powershell
python manage.py mf_pipeline_status                 # last run + next slot for every job
python manage.py mf_pipeline_run --startup --dry-run # what a backend start would run
python manage.py mf_pipeline_run --all               # run every job now, in order
python manage.py mf_pipeline_run --job mf_sync_ter   # one job through the scheduler path

python manage.py mf_sync_nav
python manage.py mf_backfill_nav                                    # mfapi.in, held schemes
python manage.py mf_backfill_nav --scope growth --max-requests 2000 # wider mfapi.in backfill
python manage.py mf_backfill_nav --source archive --db-path D:\mf-data\funds.db
python manage.py mf_nav_gap_check --max-requests 100
python manage.py mf_sync_ter [--month 10-2026]
python manage.py mf_sync_aum
python manage.py mf_sync_risk [--month 2026-10] [--skip-tracking-error]
python manage.py mf_sync_risk_free                                 # inbox / URL
python manage.py mf_sync_risk_free --rate 6.85 --date 2026-11-19   # manual entry
python manage.py mf_sync_benchmark
python manage.py mf_sync_holdings [--amc hdfc]
python manage.py mf_calc_ratios [--as-of 2026-11-20] [--scope held] [--scheme-code 118989]
~~~

A command exits non-zero only when its run is `FAILED`. Every run, manual or scheduled, is logged in `mf_source_runs`.

**Archive backfill (one-time).** Download `funds.db.zst` from the captn3m0/historical-mf-data GitHub releases, extract it (`unzstd funds.db.zst`), then pass `--db-path`. The reader accepts the NAV table under either name the project documents (`nav` or `funds`). It inserts only dates missing from `AMFIMasterNAV`, under source `AMFI_HIST_ARCHIVE`, and skips any scheme whose archive ISINs disagree with the AMFI master.

## Operator inputs (no URLs are guessed)

Some sources have no documented, licence-cleared machine endpoint. For those, the pipeline reads files you drop in, or URLs you configure, and logs `SKIPPED NOT_CONFIGURED` otherwise:

| Input | Location / config | Format |
|---|---|---|
| Benchmark TRI | `backend/data/mf_inbox/benchmark_tri/*.csv` or `backend/data/mf_config/benchmark_sources.json` → `[{"url": "...", "source_name": "NSE_INDICES_TRI"}]` | Index name/code, date, TRI columns (same as `sync_benchmark_tri`) |
| 91D T-bill | `backend/data/mf_inbox/risk_free/*.csv/.xlsx`, `PWMS_MF_RISK_FREE_URL`, or `mf_sync_risk_free --rate` | Date column + yield/rate column |
| AMC holdings | `backend/data/mf_inbox/holdings/<amc_code>/YYYY-MM-DD[__<amfi_scheme_code>][__note].xlsx` or `backend/data/mf_config/holdings_sources.json` → `{"hdfc": {"url_template": "https://…/{month}-{yyyy}.xlsx"}}` | Monthly portfolio workbook. Date = portfolio date; scheme code only for single-scheme files |

AMC codes: `hdfc`, `icici_prudential`, `sbi`, `nippon_india`, `uti`. URL-template placeholders: `{yyyy} {yy} {mm} {dd} {mon} {MON} {month} {MONTH} {month_lower}`. Each file is hashed and imported once. `backend/data/` is git-ignored.

## Identity rules

- **ISIN first, AMFI scheme code second.** NAVAll, mfapi.in, the captn3m0 archive and AAUM (keyed by `AMFI_Code`) are joined on code/ISIN.
- **AMFI TER, risk-parameter and tracking-error feeds carry no ISIN.** They name the *parent* scheme ("HDFC Mid Cap Fund") and use their own IDs (NSDL scheme code, AMFI risk `SchemeID`). `mf_scheme_identity_map` links each external ID to AMFI scheme codes through AMFI's own scheme-data file (`DownloadSchemeData_Po.aspx`). The match must be an exact normalised parent name, and only names belonging to a single AMC are linked. The link is stored, so later loads join by stored IDs.
  - Ambiguous or unknown names are logged in the run metadata (`identity_problems`), never guessed.
  - Rows added through admin/shell with `match_method=MANUAL`, or set to `status=REJECTED`, always win over the automatic match.
- Holdings files use scheme code, then scheme ISIN, then the existing `resolve_master_scheme` rule (exact unique name, only when the file has no code or ISIN).

## Field dictionary (new columns)

All decimals are stored to 4 places (the frontend formats to 2). `NULL` means not available or insufficient data, never zero. Every value also has an entry in `field_sources` (source, source_url, as_of_date, fetched_at).

| Group | Columns | Source / notes |
|---|---|---|
| Latest NAV | `nav`, `nav_as_of`, `nav_source` | AMFIMasterNAV (official NAVAll preferred over mfapi.in/archive on the same date) |
| TER | existing `ter_regular_pct`, `ter_direct_pct`, `ter_as_of` + new `ter_source` | AMFI **Total TER** for both plans, applied to every plan/option of the parent scheme (`AMFI_TER`) |
| AUM | existing `aum_cr`, `aum_type`, `aum_as_of`, `aum_source` | `AMFISchemeAAUM` ← AMFI scheme-wise AAUM (₹ lakh ÷ 100 → ₹ crore), `QUARTERLY_AVG` |
| AMFI risk | `amfi_std_dev_pct`, `amfi_benchmark_std_dev_pct`, `amfi_beta`, `amfi_sharpe`, `amfi_risk_as_of`, `amfi_risk_source` | AMFI Disclosure of Risk Parameters; raw rows in `mf_amfi_risk_parameter`. `amfi_sharpe` stays NULL (see gaps) |
| AMFI tracking error | `amfi_tracking_error_regular_pct`, `…_direct_pct`, `…_as_of`, `…_source` | AMFI tracking error (index funds/ETFs) |
| Calculated ratios | `calc_std_dev_{1y,3y}_pct`, `calc_sharpe_{1y,3y}`, `calc_sortino_{1y,3y}`, `calc_beta_{1y,3y}`, `calc_alpha_{1y,3y}_pct`, `calc_risk_free_rate_pct`, `calc_benchmark_index_code`, `calc_ratios_as_of`, `calc_ratios_source` | `mf_ratios.py` (below). Growth options only |
| Holdings summary | `top10_holdings` (JSON), `holdings_as_of`, `holdings_source` + existing `top10_concentration_pct`, `sector_allocation`, `equity_pct`, `debt_pct`, `cash_pct`, `others_pct` | Detail rows in `mf_master_holdings` (one per security per scheme per portfolio date) |

The pre-existing `std_dev_pct`, `sharpe`, `sortino`, `beta` and `alpha_pct` columns are **unchanged**; the existing builder still fills them with its 3Y method. The `calc_*` columns are the spec's "calc_sharpe/calc_beta" set, and `amfi_*` is the "amfi_sharpe/amfi_beta" set.

**Ratio method (`mf_ratios.py`).** Daily NAV returns over trailing 1Y/3Y windows; annualised SD = sample SD × √252; Rp/Rm = CAGR over the window. Sharpe = (Rp − Rf)/σ. Sortino uses downside deviation below the daily Rf. β = cov/var on dates where fund and benchmark both have values. α = Rp − [Rf + β(Rm − Rf)]. A window must start within 10 days of its nominal start and contain at least 80% of the expected daily returns. Rf is the latest stored 91D T-bill rate on or before the as-of date. If none is stored, it falls back to 6.5% (the existing builder's default) and flags `RISK_FREE_RATE_DEFAULT_USED`.

**Other tables.**
- `mf_risk_free_rate` — tenor, date, rate_pct, source.
- `mf_scheme_identity_map` — source, external_id, scheme, match_method, status.
- `mf_amfi_risk_parameter` — raw AMFI risk rows plus JSON.
- `mf_source_runs` (ingestion log). The spec's `mf_ingestion_log` maps onto it: job_name → `command`, finished_at → `completed_at`, error_message → `errors`. New columns: `rows_failed`, `source_file_hash`, and status `SKIPPED` (with `metadata.skip_reason`).
- `AMFIMasterScheme.navall_active` / `navall_inactive_since` — set after more than 7 days missing from NAVAll, and cleared when the scheme reappears. Rows are never deleted, and the existing `is_active` flag is untouched.

## Known gaps and decisions

- **AMFI publishes no Sharpe ratio.** AMFI's risk-parameter feed is SEBI's mid/small-cap disclosure. It covers only categories 17 (Mid Cap) and 18 (Small Cap), about 70 schemes, and contains portfolio/benchmark SD and portfolio beta, but no Sharpe and no ISIN. `amfi_sharpe` stays NULL; `calc_sharpe_*` covers every Growth scheme.
- **amfipy docs vs reality.** amfipy 0.3.0's data dictionary lists ISIN/Sharpe fields that the live API doesn't return. `ter.fetch()` also ignores pagination (it returns 10 of ~43,000 rows), so the pipeline uses `ter.download_excel()`, which is complete. Parsers were built from live responses captured on 23-Nov-2026.
- **AUM is quarterly.** AMFI publishes scheme-wise AUM only as quarterly averages; there is no scheme-wise monthly series, so every load is `QUARTERLY_AVG`.
- **Tracking error coverage.** On 23-Nov-2026 AMFI's `tracking-error-data?MF_ID=all` returned only 34 rows (one AMC). The job loads whatever AMFI returns.
- **Benchmark TRI and T-bill.** No URL is guessed (NSE/BSE licence terms and the absence of an RBI/FBIL machine endpoint, as already noted in the existing source map). Until files or URLs are configured, `calc_beta_*`/`calc_alpha_*` stay NULL and Rf uses the 6.5% default.
- **Benchmark mapping** reuses the existing rule: the snapshot's `benchmark_name` (from factsheet imports) must match exactly one `MFBenchmarkTRI.index_name`.
- **Holdings parsers.** Only HDFC, ICICI Prudential, SBI, Nippon India and UTI have parsers. Other held AMCs are listed in the run's `unsupported_amcs` and make the run `PARTIAL`. To add one, subclass `AMCPortfolioParser` in `holdings_registry.py` and decorate it with `@register`.
- **Parent-name bridge.** TER and risk data reach schemes through an exact match between AMFI parent-scheme names in two AMFI files. On 23-Nov-2026, 2,191 of 2,193 TER schemes resolved; the two unmatched were Navi index funds missing from AMFI's scheme-data file.
- **mfapi.in / AMFI terms.** Requests are rate-limited, identified by User-Agent and cached. Review each provider's current terms before widening `PWMS_MF_BACKFILL_SCOPE` or the gap-check cap.
- **API shape.** `GET /api/mutual-fund-analytics/masterdata/` serializes all model fields, so the new columns appear as additional keys. Existing keys and values are unchanged.
