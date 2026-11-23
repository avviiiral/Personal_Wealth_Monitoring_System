# Global Mutual-Fund Masterdata — Operations Runbook

This module builds dated, market-wide scheme snapshots keyed to the global \`AMFIMasterScheme\` universe. It is deliberately separate from family-scoped mutual-fund holdings and transactions. It does not change a household's units, cost basis, XIRR or portfolio ownership.

## Data and safety rules

- Run commands from \`backend/\` using the project's virtual environment.
- Refresh the global AMFI/SEBI universe before scheme-level imports. Keep the source's observation date separate from the download/fetch timestamp.
- Scheme identity resolution is ISIN-first. A supplied but unknown ISIN is not name-matched. Exact normalized-name fallback is accepted only when one active AMFI scheme matches; ambiguous rows are skipped and logged.
- Missing inputs remain null. Do not turn absent TER, AUM, riskometer, inception date, benchmark TRI or portfolio weights into zeroes.
- AMFI quarterly AAUM is a quarterly average in INR crore. It is not month-end AUM. Month-end AUM must come from a source explicitly identifying that measure and its units.
- TER is stored separately for Direct and Regular plans. Do not merge Base TER with brokerage, statutory levies or transaction costs.
- NAV CAGR/volatility/Sharpe/Sortino/drawdown are different from benchmark-relative beta/alpha/active-risk metrics. Personal portfolio XIRR is a separate household cash-flow calculation and is not written by this market-wide builder.
- Benchmark-relative ratios require a benchmark TRI series with enough aligned observations. A price index is not a TRI. The default benchmark window is three years; inadequate history remains null with a quality flag.
- Market-cap attribution uses the AMFI half-year list that is effective on the requested snapshot date. Unclassified equity exposure remains in \`other_cap_pct\`.
- Do not automate bulk extraction from Moneycontrol, Value Research, Morningstar or other third-party sites unless the applicable terms/licence and permissions allow it. Use official AMFI/SEBI/AMC disclosures or a separately authorized data feed.
- BSE/NSE index data must be ingested only under the relevant exchange data-use terms/licence. Until an authorized feed is configured, provide a licensed/approved TRI file through \`--from-file\`.
- URL downloads are cached under \`backend/data/mf_raw/<source>/<date>/\`; the path is git-ignored. Never commit raw disclosure files, user-provided data, credentials, API keys or provider caches.

## Initial setup and global universe

Run migrations and refresh the market-wide scheme universe:

\`\`\`powershell
python manage.py migrate
python manage.py ingest_sebi_mf_universe
\`\`\`

Refresh the universe before new imports so scheme codes, ISINs, AMC, plans and SEBI categories are current. The existing AMFI/MFapi workflow supplies global NAV history used by the analytics builder.

## Quarterly average AUM (AMFI)

The existing AMFI scheme-wise report contains quarterly average AUM in Rs. lakh. The importer converts lakh to crore and keeps the quarter start/label.

\`\`\`powershell
python manage.py sync_mf_aum --from-file "D:\\mf-data\\amfi-aaum-q3-2026.html" --source-url "https://portal.amfiindia.com/AUMReport_Rpt_Po.aspx?dtAUM=01-Jul-2026&mf=0&qt=July+-+September+2026&rpt=scwise" --date 2026-07-01 --aum-type QUARTERLY_AVG --dry-run
python manage.py sync_mf_aum --from-file "D:\\mf-data\\amfi-aaum-q3-2026.html" --source-url "https://portal.amfiindia.com/AUMReport_Rpt_Po.aspx?dtAUM=01-Jul-2026&mf=0&qt=July+-+September+2026&rpt=scwise" --date 2026-07-01 --aum-type QUARTERLY_AVG
\`\`\`

The supplied date is the quarter start. The builder labels the observation by the actual quarter end. A not-yet-completed quarter is not eligible for an earlier as-of snapshot.

## Month-end scheme AUM

Use only an AMC disclosure that explicitly identifies a month-end value. The date must be the final calendar day of the month. If the amount column does not declare rupees/lakh/crore in its header, provide \`--amount-unit\` explicitly rather than guessing.

\`\`\`powershell
python manage.py sync_mf_aum --from-file "D:\\mf-data\\hdfc-month-end-aum-oct-2026.xlsx" --source-url "https://www.hdfcfund.com/" --date 2026-10-31 --aum-type MONTH_END --amc HDFC --amount-unit CRORE --dry-run
python manage.py sync_mf_aum --from-file "D:\\mf-data\\hdfc-month-end-aum-oct-2026.xlsx" --source-url "https://www.hdfcfund.com/" --date 2026-10-31 --aum-type MONTH_END --amc HDFC --amount-unit CRORE
\`\`\`

The sample AMC URL above is illustrative; replace it with the exact official disclosure page/document URL used for the downloaded file. Never label monthly average AUM or quarterly AAUM as month-end AUM.

## TER

Import a dated TER file with scheme identity and plan information. Direct and Regular TER values remain separate.

\`\`\`powershell
python manage.py sync_mf_ter --from-file "D:\\mf-data\\amfi-ter-2026-10.tsv" --source-url "https://www.amfiindia.com/ter-of-mf-schemes" --date 2026-10-31 --dry-run
python manage.py sync_mf_ter --from-file "D:\\mf-data\\amfi-ter-2026-10.tsv" --source-url "https://www.amfiindia.com/ter-of-mf-schemes" --date 2026-10-31
\`\`\`

## Monthly scheme portfolios

The initial adapter registry supports HDFC, ICICI Prudential, SBI, Nippon India and UTI. Portfolio data remains instrument-level and includes equity, debt, cash, derivatives, fund units and other instruments. Missing ratings, yields, sectors, quantities or weights remain null.

For a single-scheme disclosure without scheme identity columns, use its exact AMFI scheme code:

\`\`\`powershell
python manage.py sync_mf_portfolios --from-file "D:\\mf-data\\hdfc-scheme-portfolio-2026-10.xlsx" --source-url "https://www.hdfcfund.com/" --date 2026-10-31 --amc hdfc --scheme-code 118989 --dry-run
python manage.py sync_mf_portfolios --from-file "D:\\mf-data\\hdfc-scheme-portfolio-2026-10.xlsx" --source-url "https://www.hdfcfund.com/" --date 2026-10-31 --amc hdfc --scheme-code 118989
\`\`\`

For a multi-scheme file, the source must contain scheme code, scheme ISIN or an exact scheme name column. Do not fuzzy-match a portfolio to a scheme. Re-importing the same scheme/date/security is an idempotent update.

## Scheme facts, riskometer and offer-document attributes

Use official AMC factsheets, scheme information documents, key information memoranda, addenda and published scheme summary data. Supported fields include primary benchmark, inception date, fund managers, scheme/benchmark riskometer, exit-load text, minimum lumpsum/SIP, P/E, P/B, dividend yield, portfolio turnover, YTM and duration/maturity fields.

\`\`\`powershell
python manage.py sync_mf_scheme_info --from-file "D:\\mf-data\\amc-scheme-info-2026-10.xlsx" --source-url "https://www.hdfcfund.com/" --date 2026-10-31 --amc HDFC --dry-run
python manage.py sync_mf_scheme_info --from-file "D:\\mf-data\\amc-scheme-info-2026-10.xlsx" --source-url "https://www.hdfcfund.com/" --date 2026-10-31 --amc HDFC
\`\`\`

The URL shown is an example AMC landing page; retain the exact page/document URL for the actual disclosure. Multiple managers are stored as a list. Do not infer missing fields from a third-party summary.

## Benchmark TRI history

Benchmark-relative beta, alpha, Treynor, R-squared, tracking error, information ratio and upside/downside capture require daily TRI observations matched to the scheme's declared primary benchmark.

\`\`\`powershell
python manage.py sync_benchmark_tri --from-file "D:\\mf-data\\nifty-large-midcap-250-tri.csv" --source-url "https://www.niftyindices.com/" --date 2026-10-31 --source-name NSE_INDICES_TRI --dry-run
python manage.py sync_benchmark_tri --from-file "D:\\mf-data\\nifty-large-midcap-250-tri.csv" --source-url "https://www.niftyindices.com/" --date 2026-10-31 --source-name NSE_INDICES_TRI
\`\`\`

The example URL is an index landing page; use the exact authorized TRI file URL in production. The current matching is exact normalized benchmark-name matching. No fuzzy index mapping is performed. The default window is three years and the engine requires aligned observations; insufficient history produces null ratios and a data-quality flag.

## AMFI half-yearly market-cap classification

Load the official AMFI ISIN-level Large/Mid/Small Cap list with its effective half-year, for example \`2026-H2\`.

\`\`\`powershell
python manage.py sync_mf_marketcap --from-file "D:\\mf-data\\amfi-market-cap-2026-H2.xlsx" --source-url "https://www.amfiindia.com/" --date 2026-11-01 --effective-period 2026-H2 --dry-run
python manage.py sync_mf_marketcap --from-file "D:\\mf-data\\amfi-market-cap-2026-H2.xlsx" --source-url "https://www.amfiindia.com/" --date 2026-11-01 --effective-period 2026-H2
\`\`\`

Use the exact AMFI document URL for the downloaded list. The builder uses the half-year effective on the snapshot date; a future classification period cannot affect a historical snapshot.

## Build dated scheme snapshots

After importing sources, build the combined snapshot:

\`\`\`powershell
python manage.py build_mf_masterdata --as-of 2026-10-31 --dry-run
python manage.py build_mf_masterdata --as-of 2026-10-31
\`\`\`

Optional calculation/filter inputs:

\`\`\`powershell
python manage.py build_mf_masterdata --as-of 2026-10-31 --amc HDFC --limit 25 --risk-free-rate 6.5 --ratio-window-years 3 --dry-run
\`\`\`

The builder:
1. Creates or updates one \`MFMasterData\` row per global AMFI scheme and snapshot date.
2. carries forward dated disclosed facts while preserving the original \`field_sources\` lineage;
3. selects the newest eligible AUM and preserves \`aum_type\` and \`aum_as_of\`;
4. reuses the existing NAV performance implementation for returns and risk;
5. calculates benchmark-relative ratios only from aligned NAV and TRI history;
6. derives asset allocation, sector split, rating mix, top-10 concentration and cap exposure from the latest available holdings disclosure; and
7. reports field coverage and source-run diagnostics.

A same-date rebuild is idempotent. Derived values are recalculated; missing current inputs do not retain stale calculated ratios. \`--dry-run\` performs read/parse/calculation work but writes neither masterdata rows nor a source-run record.

## Read-only API

Authenticated clients can retrieve the latest snapshot per AMFI scheme:

\`\`\`http
GET /api/mutual-fund-analytics/masterdata/?page=1&page_size=50
GET /api/mutual-fund-analytics/masterdata/?category=Large%20Cap&amc=HDFC&plan=Direct%20Plan
GET /api/mutual-fund-analytics/masterdata/?q=HDFC
GET /api/mutual-fund-analytics/masterdata/?scheme_code=118989
\`\`\`

Supported filters: \`category\` (SEBI Level 3), \`amc\`, \`plan\`, \`scheme_code\`, \`aum_type\` and \`q\` (scheme name/code, AMC or ISIN). Page size defaults to 50 and is capped at 200. This is a GET-only endpoint. It reads the local database and does not download data, call IndianAPI or consume provider quota.

The response includes the snapshot's field-level \`field_sources\` and \`data_quality_flags\`. Clients should display the AUM type/date and source dates, and should not treat missing ratios as zero.

## Suggested cadence

| Data | Cadence | Notes |
|---|---|---|
| AMFI universe and latest NAV | Business-day daily | Refresh before snapshot build |
| NAV history/cache | Daily or when coverage is insufficient | Reuse the existing bounded MFapi/AMFI cache |
| Monthly AMC portfolio | Monthly after disclosure publication | SEBI portfolio timing applies; use the actual disclosure date |
| Debt holdings/YTM | Fortnightly when an AMC publishes them | Preserve the exact disclosure date |
| TER | Each available AMFI/AMC publication | Direct/Regular remain separate |
| Quarterly AAUM | Quarterly after AMFI publication | Store as QUARTERLY_AVG |
| Month-end AUM | Monthly only when explicitly disclosed | Store as MONTH_END |
| Benchmark TRI | Daily when an authorized data file/feed is available | Match the declared benchmark exactly |
| AMFI cap classification | Half-yearly | Store the effective period |
| Riskometer/SID/KIM facts | On change and with factsheet refresh | Preserve dated lineage |

## Scheduled execution

Schedule source imports before the snapshot builder; do not run the builder concurrently with imports. For Windows Task Scheduler, use separate scripts so the as-of date and quarter/month-end dates are explicit.

Example daily universe refresh:

\`\`\`powershell
$Project = "D:\\PWMS\\Personal_Wealth_Monitoring_System"
$Python = Join-Path $Project "backend\\venv\\Scripts\\python.exe"
$Backend = Join-Path $Project "backend"
$Action = New-ScheduledTaskAction -Execute $Python -Argument "manage.py ingest_sebi_mf_universe" -WorkingDirectory $Backend
$Trigger = New-ScheduledTaskTrigger -Daily -At 7:15AM
Register-ScheduledTask -TaskName "PWMS-AMFI-Universe" -Action $Action -Trigger $Trigger -Description "Refresh AMFI/SEBI global mutual-fund scheme universe."
\`\`\`

Schedule \`build_mf_masterdata\` after the applicable source tasks complete. Monthly/quarterly source downloads should only run after the exact disclosure is available and its observation date is verified.

## Troubleshooting

- **No rows parsed:** verify the official source file, extension, worksheet/table and headers. Run with \`--dry-run\` first.
- **Unmatched/ambiguous scheme:** check AMFI scheme code/ISIN and the exact source identity. Do not use fuzzy matching to force a result.
- **AUM magnitude looks wrong:** verify the header unit and \`aum_type\`; do not guess units or compare quarterly average with month-end as if they were the same measure.
- **No benchmark ratios:** verify the scheme's declared benchmark, TRI index name, observation coverage and same-date NAV/TRI overlap. Short history remains null.
- **Cap exposure is incomplete:** verify the correct effective half-year file and security ISINs; unmatched equity is reported in \`other_cap_pct\`.
- **Portfolio weight total differs from 100:** inspect source rows and quality flags. Do not scale or redistribute missing holdings weights to force 100%.
- **Run diagnostics:** inspect \`MFSourceRun\` in Django admin. URL downloads are cached under the ignored \`backend/data/mf_raw/\` path.
- **IndianAPI quota:** this pipeline does not make per-scheme IndianAPI requests. The separate IndianAPI client retains its persistent 400-request monthly hard cap.
