# Troubleshooting

## Dashboard chart warnings

### `No historical data available for wealth chart`

The Dashboard expects the historical endpoint response to contain a `results` collection. Confirm the API response before changing the backend. The Angular chart should only render after both the view and historical data are available.

### `Allocation chart canvas not available`

The allocation canvas exists only after the Dashboard's loading view has been replaced. Chart creation must therefore wait for Angular view initialization and investment-summary data.

## Slow `/api/portfolio/tree/`

The portfolio tree is a shared data source for Dashboard and Portfolio. When investigating latency, use browser Network timing and inspect Django-side query/calculation time rather than assuming the 50–60 KB response size is the problem.

The tree service groups transactions in memory and builds the hierarchy. Avoid introducing new per-asset database queries into this path without measuring the effect.

## GitHub Actions

The generic GitHub Python workflow must match the repository layout. PWMS dependencies are stored under `backend/requirements.txt`, and backend tests are run through Django's `manage.py` test runner. Use Python versions compatible with the Django version pinned by the project.

## SQLite corruption

A `SQLITE_CORRUPT` error indicates database-file corruption and should be treated separately from application-code errors. Before deleting a development database, back up the file and confirm that the data can be recreated/imported. Never delete a production database as a first troubleshooting step.

## 429 / AI rate limits

Gemini 429 responses indicate the configured API quota/rate limit has been exceeded. Reduce unnecessary AI calls, respect the configured request delay, and inspect usage logs before increasing call volume.

## Authentication issues

PWMS uses Django session authentication with cookies and CSRF protection. When browser requests return 401/403, check the session cookie, CSRF token, backend logs and the authenticated user's role/family permissions before changing frontend routing.

## Useful browser checks

- Network: verify endpoint status, response payload and timing.
- Console: check for JavaScript/runtime errors after a hard refresh.
- Preserve log / Keep log: disable when you need a clean single-page-load trace.
- Sort Network by Time to find backend latency outliers.
