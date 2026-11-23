# Mutual Fund Masterdata — Operations Runbook

This runbook operates the global, market-wide scheme masterdata pipeline. It is keyed to **AMFIMasterScheme** and is separate from family-scoped investment holdings and transactions. It does not modify household units, cost basis, XIRR, ownership or existing Mutual Fund Intelligence dashboard values.

## Operating rules

- Run commands from the repository's **backend/** directory and activate the project's virtual environment.
- Refresh the AMFI scheme universe before importing any scheme-dependent disclosure.
- Keep disclosure observation date, fetch timestamp and source URL separate.
- Match scheme identity by ISIN first, then exact normalized scheme name only when unique. An unknown supplied ISIN must not fall back to a name. Ambiguous/unmatched rows are skipped and reported.
- A local file import requires **--source-url** with the official disclosure page/document URL. A download URL passed through **--url** is stored automatically.
- Missing fields remain null. Never coerce missing TER, AUM, benchmark ratios, holdings or ratings into zero.
- Quarterly average AUM and month-end AUM are different observations; retain the explicit aum_type and aum_as_of fields. Do not compare them as identical measures.
- Direct and Regular TER are stored separately.
- Benchmark-relative ratios require TRI—not a price index—and an aligned daily NAV/TRI window. The default window is three years. Insufficient history remains null with a data-quality flag.
- Market-cap attribution uses the AMFI half-year list effective on the requested snapshot date. Unclassified equity remains in other_cap_pct.
- Public access does not grant a general right to bulk scrape or republish data. Do not automate Moneycontrol, Value Research, Morningstar or another third-party source unless its current terms/licence and written permissions expressly allow it.
- NSE/BSE TRI ingestion must comply with the relevant exchange data-use conditions and licence. Until an authorized feed is configured, import a permitted TRI file through **--from-file**.
- Downloaded raw files are cached under **backend/data/mf_raw/<source>/<date>/** and are git-ignored. Do not commit raw source files, caches, API keys, credentials or user data.
- This pipeline does not make per-scheme IndianAPI requests. The existing persistent IndianAPI monthly request budget and 400-request hard cap remain unchanged.

## Initial database and scheme-universe setup

Apply migrations and refresh the global AMFI/SEBI scheme universe:

~~~powershell
python manage.py migrate
python manage.py ingest_sebi_mf_universe
~~~

Run the universe refresh before new imports. Review its imported, skipped and unmapped counts. The existing AMFI/MFapi NAV-history cache supplies observations for the NAV-derived performance calculations; the masterdata collectors do not create a new per-scheme IndianAPI fan-out.

## AUM collection

### Quarterly average AUM (AMFI)

AMFI's scheme-wise report expresses quarterly AAUM in Rs. lakh. The collector converts that value to INR crore and stores it as **QUARTERLY_AVG**. The **--date** value is the quarter start (Jan 1, Apr 1, Jul 1 or Oct 1); the snapshot uses the quarter's actual end date as aum_as_of.

~~~powershell
python manage.py sync_mf_aum --from-file "D:\mf-data\amfi-aaum-q3-2026.html" --source-url "https://portal.amfiindia.com/AUMReport_Rpt_Po.aspx?dtAUM=01-Jul-2026&mf=0&qt=July+-+September+2026&rpt=scwise" --date 2026-07-01 --aum-type QUARTERLY_AVG --dry-run
python manage.py sync_mf_aum --from-file "D:\mf-data\amfi-aaum-q3-2026.html" --source-url "https://portal.amfiindia.com/AUMReport_Rpt_Po.aspx?dtAUM=01-Jul-2026&mf=0&qt=July+-+September+2026&rpt=scwise" --date 2026-07-01 --aum-type QUARTERLY_AVG
~~~

The existing AMFI report parser is reused; the command does not make a second HTTP request to the same report. Quarterly AAUM is not a month-end balance.

### Month-end scheme AUM

Use only an AMC disclosure that explicitly identifies net assets/AUM as at the final calendar day of the month. The amount header must identify rupees, lakh or crore, or the operator must provide **--amount-unit** explicitly. The collector rejects ambiguous units rather than guessing.

~~~powershell
python manage.py sync_mf_aum --from-file "D:\mf-data\hdfc-month-end-aum-oct-2026.xlsx" --source-url "https://www.hdfcfund.com/statutory-disclosure/portfolio/monthly-portfolio" --date 2026-10-31 --aum-type MONTH_END --amc HDFC --amount-unit CRORE --dry-run
python manage.py sync_mf_aum --from-file "D:\mf-data\hdfc-month-end-aum-oct-2026.xlsx" --source-url "https://www.hdfcfund.com/statutory-disclosure/portfolio/monthly-portfolio" --date 2026-10-31 --aum-type MONTH_END --amc HDFC --amount-unit CRORE
~~~

Replace the illustrative example path/page with the exact official disclosure URL used for the file. The date must be the actual month-end; monthly average AUM must not be stored as MONTH_END.

## TER collection

Import the dated AMFI/AMC disclosure containing scheme identity, plan and the published TER percentage. Direct and Regular are written to separate fields.

~~~powershell
python manage.py sync_mf_ter --from-file "D:\mf-data\amfi-ter-2026-10.tsv" --source-url "https://www.amfiindia.com/ter-of-mf-schemes" --date 2026-10-31 --dry-run
python manage.py sync_mf_ter --from-file "D:\mf-data\amfi-ter-2026-10.tsv" --source-url "https://www.amfiindia.com/ter-of-mf-schemes" --date 2026-10-31
~~~

Do not merge Base TER with brokerage, statutory levies or transaction costs unless the source explicitly defines a combined TER field.

## Monthly scheme portfolio holdings

The initial adapter registry supports HDFC, ICICI Prudential, SBI, Nippon India and UTI. Instrument rows are stored against the global AMFI scheme master, not the family holdings table. Equity, debt, cash, fund units, derivatives and other instruments are retained. Unknown or missing quantity, rating, YTM, sector or weight remains null.

For a one-scheme disclosure that does not include scheme identity columns, provide the exact AMFI scheme code:

~~~powershell
python manage.py sync_mf_portfolios --from-file "D:\mf-data\hdfc-scheme-portfolio-2026-10.xlsx" --source-url "https://www.hdfcfund.com/statutory-disclosure/portfolio/monthly-portfolio" --date 2026-10-31 --amc hdfc --scheme-code 118989 --dry-run
python manage.py sync_mf_portfolios --from-file "D:\mf-data\hdfc-scheme-portfolio-2026-10.xlsx" --source-url "https://www.hdfcfund.com/statutory-disclosure/portfolio/monthly-portfolio" --date 2026-10-31 --amc hdfc --scheme-code 118989
~~~

A multi-scheme file must include scheme code, scheme ISIN or exact scheme name. The scheme must belong to the selected AMC. Re-importing the same scheme/date/security is an idempotent update. Run a dry-run before importing a newly encountered AMC workbook layout.

### Optional MF Forecast holdings API (supplementary source)

MF Forecast documents a scheme endpoint, `GET /funds/{amfi_code}/holdings`, with AUM, expense ratio, allocation and security-level holding fields. It can supplement portfolio coverage where a supported AMC disclosure file is not available. The API base URL and authentication header must be copied from the provider's current documentation; do not guess either value.

Configure the local `backend/.env` using the variables in `.env.example`, then begin with a small test:

~~~powershell
python manage.py sync_mfforecast_holdings --scheme-code 128629 --dry-run
python manage.py sync_mfforecast_holdings --scheme-code 128629
python manage.py sync_mfforecast_holdings --amc HDFC --limit 25 --dry-run
~~~

The command caps each run at 50 scheme requests, uses a one-request-per-second host throttle, caches raw JSON outside Git, and logs a source run. MF Forecast's example does not supply a portfolio disclosure date, so holdings are tagged `FETCHED_AT` rather than `DISCLOSURE`. Dated AMC disclosures always take precedence in consolidated portfolio analytics. The provider's AUM and expense ratio are retained in `MFExternalPortfolioSnapshot` but are not copied to canonical month-end AUM or Direct/Regular TER fields until their definitions and dates are verified.

### MF Forecast batch planning and resumable coverage

For broad Direct-Growth coverage, plan a batch across all AMCs instead of issuing one command per scheme:

~~~powershell
python manage.py plan_mfforecast_batch --limit 50
~~~

The planner reads active Direct-Growth schemes from AMFIMasterScheme, selects schemes round-robin across AMCs, and caps a batch at the importer's 50-request limit. It makes no API requests and writes no database records. An optional repeated AMC filter narrows the batch, for example --amc HDFC --amc SBI.

Review the generated dry-run command before running the live command. Live imports store one outcome per attempted scheme in MFSourceRun metadata: **IMPORTED**, **INCOMPLETE**, **NOT_FOUND** (HTTP 404), or **ERROR**. The next planner run skips already imported schemes and previously recorded not-found/incomplete/error outcomes, preventing repeated requests in ordinary batches. A dry-run does not record outcomes; only live runs advance coverage state.

Retry only when there is a reason to recheck a provider outcome:

~~~powershell
python manage.py plan_mfforecast_batch --retry-not-found --limit 50
python manage.py plan_mfforecast_batch --retry-incomplete --retry-errors --limit 50
~~~

Use --include-previously-synced only for an intentional refresh of schemes with prior MF Forecast outcomes or snapshots. The planner does not claim that a provider 404 means the mutual-fund scheme has no portfolio; it records only that the endpoint did not return a provider record at that time.

IMPORTED means rows parsed and provider holding count reconciled (when the provider supplied a count). Weight gaps and classification overrides remain in the snapshot's quality flags and must still be reviewed; structural import success is not a claim that the provider portfolio totals 100%.

Before scheduling broader collection, confirm the provider's current API plan, rate limits, storage and downstream-use terms. See [MF Forecast API integration](mfforecast_api.md) for configuration and data-quality details.

## Scheme information, riskometer and factsheet attributes

Use official AMC factsheets, SID/KIM, scheme summaries and addenda for primary benchmark, inception date, fund managers, scheme/benchmark riskometer, exit load, minimum lumpsum/SIP, P/E, P/B, dividend yield, turnover, YTM and debt duration/maturity facts.

~~~powershell
python manage.py sync_mf_scheme_info --from-file "D:\mf-data\hdfc-factsheet-2026-10.xlsx" --source-url "https://www.hdfcfund.com/" --date 2026-10-31 --amc HDFC --dry-run
python manage.py sync_mf_scheme_info --from-file "D:\mf-data\hdfc-factsheet-2026-10.xlsx" --source-url "https://www.hdfcfund.com/" --date 2026-10-31 --amc HDFC
~~~

Use the exact official factsheet/document URL for the actual downloaded file. Multiple managers are preserved as a list. Do not infer inception dates, exit-load conditions, riskometer labels or benchmark names from third-party summaries.

## Benchmark TRI history

Benchmark-relative beta, alpha, Treynor, R-squared, tracking error, information ratio and upside/downside capture require the scheme's declared primary benchmark and a daily total-return-index series with matching dates. A price index is not an acceptable substitute for TRI.

~~~powershell
python manage.py sync_benchmark_tri --from-file "D:\mf-data\nifty-large-midcap-250-tri.csv" --source-url "https://www.niftyindices.com/" --date 2026-10-31 --source-name NSE_INDICES_TRI --dry-run
python manage.py sync_benchmark_tri --from-file "D:\mf-data\nifty-large-midcap-250-tri.csv" --source-url "https://www.niftyindices.com/" --date 2026-10-31 --source-name NSE_INDICES_TRI
~~~

The example is an index-information page; record the exact authorized TRI download/feed URL in production. Benchmark mapping is exact normalized name matching—no fuzzy benchmark mapping is performed. Three years is the default calculation window; inadequate aligned history remains null with an explicit quality flag.

## AMFI half-year market-cap classification

Load the AMFI ISIN-level Large/Mid/Small Cap list and preserve its half-year effective period, for example **2026-H2**.

~~~powershell
python manage.py sync_mf_marketcap --from-file "D:\mf-data\amfi-market-cap-2026-H2.xlsx" --source-url "https://www.amfiindia.com/" --date 2026-11-01 --effective-period 2026-H2 --dry-run
python manage.py sync_mf_marketcap --from-file "D:\mf-data\amfi-market-cap-2026-H2.xlsx" --source-url "https://www.amfiindia.com/" --date 2026-11-01 --effective-period 2026-H2
~~~

Replace the example page with the exact AMFI document URL used. The builder selects the half-year classification effective on its as-of date, not the newest file in the database.

## Build consolidated scheme snapshots

After importing all current sources for the chosen as-of date, build the consolidated snapshot:

~~~powershell
python manage.py build_mf_masterdata --as-of 2026-10-31 --dry-run
python manage.py build_mf_masterdata --as-of 2026-10-31
~~~

Optional filters and calculation inputs:

~~~powershell
python manage.py build_mf_masterdata --as-of 2026-10-31 --amc HDFC --limit 25 --risk-free-rate 6.5 --ratio-window-years 3 --dry-run
~~~

The builder creates or updates one row per global scheme/date. It carries forward only disclosed facts while retaining the original field-level source lineage; chooses the freshest eligible AUM while preserving its type and observation date; reuses the existing NAV performance engine for trailing returns, risk and drawdown; and computes benchmark-relative and portfolio-derived measures only when their inputs exist. Same-date rebuilds are idempotent and recompute derived fields instead of retaining stale values. Dry-run performs reads/calculations only and creates neither masterdata rows nor a source-run record.

The builder prints field coverage and records import/build diagnostics in **MFSourceRun**. Null fields mean source data is unavailable or insufficient; they are not zero.

## Read-only API

Authenticated clients may request the latest dated snapshot per AMFI scheme:

~~~http
GET /api/mutual-fund-analytics/masterdata/?page=1&page_size=50
GET /api/mutual-fund-analytics/masterdata/?category=Large%20Cap&amc=HDFC&plan=Direct%20Plan
GET /api/mutual-fund-analytics/masterdata/?q=HDFC
GET /api/mutual-fund-analytics/masterdata/?scheme_code=118989
~~~

Filters: **category** (SEBI Level 3), **amc**, **plan**, **scheme_code**, **aum_type** and **q** (scheme name/code, AMC or ISIN). Page size defaults to 50 and is capped at 200. This endpoint is GET-only, authenticated and database-only; it does not scrape sources or consume IndianAPI quota. The response includes field-level source lineage and data-quality flags.

## Suggested cadence

| Dataset | Cadence | Operating note |
|---|---|---|
| AMFI universe and latest NAV | Business-day daily | Refresh before masterdata build |
| Historical NAV cache | Daily / when history is insufficient | Reuse the existing bounded NAV cache |
| Monthly AMC portfolio | Monthly after disclosure publication | Capture the real portfolio as-of date |
| Debt holdings / YTM | Fortnightly where AMC publishes them | Keep each disclosure date separate |
| TER | Each available AMFI/AMC publication | Preserve Direct/Regular separately |
| Quarterly AAUM | Quarterly after AMFI publishes the report | Store QUARTERLY_AVG |
| Month-end AUM | Monthly only when explicitly disclosed | Store MONTH_END |
| Benchmark TRI | Daily when an authorized feed/file is available | Match primary benchmark exactly |
| AMFI cap classification | Half-yearly | Preserve effective period |
| Riskometer/SID/KIM facts | On change and with factsheet refresh | Keep disclosure date and URL |

## Scheduling

Run source imports before the snapshot builder; do not build while a source import is in progress. Keep month-end and quarter-start dates explicit in the scheduled script rather than deriving them from the execution date.

Example Windows Task Scheduler command for the daily universe refresh:

~~~powershell
$Project = "D:\PWMS\Personal_Wealth_Monitoring_System"
$Python = Join-Path $Project "backend\venv\Scripts\python.exe"
$Backend = Join-Path $Project "backend"
$Action = New-ScheduledTaskAction -Execute $Python -Argument "manage.py ingest_sebi_mf_universe" -WorkingDirectory $Backend
$Trigger = New-ScheduledTaskTrigger -Daily -At 7:15AM
Register-ScheduledTask -TaskName "PWMS-AMFI-Universe" -Action $Action -Trigger $Trigger -Description "Refresh the global AMFI/SEBI scheme universe."
~~~

Schedule the masterdata build after the daily/dated source tasks have completed. Monthly and quarterly file imports should run only once the official disclosure has been published and its observation date has been confirmed.

## Troubleshooting

- **No rows parsed:** verify the official source file, extension, worksheet/table and column headings; run with dry-run first.
- **Missing source URL:** add the exact official disclosure URL through **--source-url** for a local file.
- **Ambiguous AUM units:** verify the source header or provide **--amount-unit RUPEES**, **LAKH** or **CRORE**. Never infer the unit.
- **Unmatched scheme:** check scheme code/ISIN and the source identity. Do not fuzzy-match a scheme to force an import.
- **No benchmark ratios:** verify the primary benchmark label, TRI identifier, daily history and aligned NAV dates. Short history remains null.
- **Incomplete cap split:** verify the intended effective half-year list and each holding's ISIN. Unclassified equity is included in other_cap_pct.
- **Portfolio weights do not total 100:** inspect the disclosure and quality flags. Do not scale the known weights to hide missing rows.
- **Run diagnostics:** inspect **MFSourceRun** in Django admin. URL downloads are cached under the ignored raw-data path.
- **Provider quota:** this feature makes no per-scheme IndianAPI calls. Keep the existing persistent 400-request monthly guard intact.
