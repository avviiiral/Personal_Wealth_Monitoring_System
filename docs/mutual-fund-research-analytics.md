# Mutual Fund Analytics — Research Report Pipeline

## Source contract

The importer reads three JSON objects embedded in the supplied HTML:

- `RAW`: category reference metrics, fund-level scorecard fields, market-cap/style allocation, monthly trend arrays, sector weights, top holdings and unique ideas.
- `P2P`: point-to-point returns, coverage counts, category returns and benchmark values.
- `ATTR`: per-fund stock contributors/laggards, sector attribution and cross-fund gainers/laggards.

The importer uses Python's JSON decoder on the object literals. It never evaluates JavaScript, runs browser scripts, or imports the HTML as executable code.

## Import

From `backend/`:

```powershell
python manage.py migrate
python manage.py import_mutual_fund_research_report --file "D:\path\to\Nexedge_multicap_dashboard_Oct2026.html"
```

Optional `--report-key` allows multiple report families to be stored independently. The same key/date pair is updated on re-import. New report dates are retained as separate snapshots.

## API

Authenticated endpoint: `GET /api/watch-list/analytics/research-report/`.

Returns the latest report ordered by report date and import time. When no report exists, it returns `available: false` and an import instruction. The API does not mix this historical source snapshot with live Watch List NAV/performance data.

## Data interpretation

The source reports different counts for RAW funds, P2P-covered funds and ATTR-covered funds. These coverage differences are preserved. Missing observations remain missing; the frontend displays an em dash instead of substituting zero. Calendar-year metrics, P2P returns and attribution values retain their source units and definitions.

The original HTML contains some labels whose stated fund counts do not match the actual RAW fund array. The implementation uses the actual imported arrays for row counts and labels the counts by source dataset rather than forcing them to agree.
