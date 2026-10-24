# PWMS Production Readiness Checklist

This checklist separates source-controlled safeguards from checks that must be performed against the actual Windows production host. It must be completed before treating a release as production-ready.

## 1. Production Django security configuration

Run from the deployed backend directory in an elevated PowerShell session only if that is how the service is normally operated. Do not print or paste secret values.

```powershell
cd D:\001_PWMS_PROJECT\Personal_Wealth_Monitoring_System\backend

python manage.py check
python manage.py check --deploy

python manage.py shell -c "from django.conf import settings; print('DEBUG=', settings.DEBUG); print('SECRET_KEY_CONFIGURED=', bool(settings.SECRET_KEY)); print('ALLOWED_HOSTS_COUNT=', len(settings.ALLOWED_HOSTS)); print('SESSION_COOKIE_SECURE=', settings.SESSION_COOKIE_SECURE); print('CSRF_COOKIE_SECURE=', settings.CSRF_COOKIE_SECURE); print('SECURE_SSL_REDIRECT=', settings.SECURE_SSL_REDIRECT); print('CSRF_TRUSTED_ORIGINS_COUNT=', len(settings.CSRF_TRUSTED_ORIGINS)); print('CORS_ALLOWED_ORIGINS_COUNT=', len(settings.CORS_ALLOWED_ORIGINS)); print('SECURE_PROXY_SSL_HEADER=', settings.SECURE_PROXY_SSL_HEADER)"
```

Expected production posture:

- `DEBUG=False`.
- `SECRET_KEY_CONFIGURED=True`; never print the key itself.
- `ALLOWED_HOSTS_COUNT` is greater than zero and the configured hosts are the exact intended hostnames/IPs.
- `SESSION_COOKIE_SECURE=True` and `CSRF_COOKIE_SECURE=True` when the application is served exclusively over HTTPS.
- `SECURE_SSL_REDIRECT=True` only after confirming the TLS termination/reverse-proxy topology and testing that Django reliably recognizes HTTPS. Do not enable this blindly: incorrect proxy configuration can create redirect loops.
- `CSRF_TRUSTED_ORIGINS` and `CORS_ALLOWED_ORIGINS` contain only the exact required origins; CORS must not be widened to all origins.
- `SECURE_PROXY_SSL_HEADER` is safe only when the trusted proxy removes/overwrites any client-supplied `X-Forwarded-Proto` header. Do not expose the app server directly to untrusted clients if it relies on this header.

The shell output above reports only booleans and counts, not secret values or the host/origin lists. Review actual values locally without copying secrets into tickets or chat. Save the result and any `check --deploy` warnings in the deployment record.

Do not change production environment variables until the current proxy/TLS path and service restart/rollback procedure are confirmed.

## 2. CI and merge controls

The backend workflow runs Django checks, migration consistency, and the backend suite on SQLite and PostgreSQL. The added frontend job runs `npm ci`, a production Angular build, and frontend unit tests.

After the workflow has run on a pull request, open repository Settings and configure branch protection/rules for `main`:

- Require a pull request before merging.
- Require the status checks **Django checks and tests (SQLite)**, **Django checks and tests (PostgreSQL)**, and **Angular build and unit tests**.
- Require the branch to be up to date before merging if that fits the release process.
- Block force pushes and deletion of `main`.
- Keep administrator bypass disabled unless there is a documented emergency procedure.

GitHub ruleset/branch-protection settings are repository administration state and are not changed by a source-code commit. Confirm the exact check names after the first successful CI run before making them required.

## 3. Family-isolation regression checks

The existing family data ownership tests cover selected asset access and same-family analytics access. A new regression test checks that the transaction list includes a permitted family's transaction but excludes a transaction belonging to an unrelated family.

Before release, extend the endpoint matrix to cover reads and writes for portfolio assets/transactions, portfolio tree and reports, overall and subclass XIRR, historical reports, mutual funds, imports/exports, watchlists, filings/news, corporate actions, and settings. For each family-scoped endpoint, test same-family access, unrelated-family IDs, unauthenticated access, and privileged roles where applicable. A passing asset-detail test alone is not proof that every API is isolated.

## 4. XIRR and valuation characterization

The added pure-unit tests pin current shared cash-flow semantics:

- BUY/SIP/DEPOSIT outflows include fees and are negative.
- SELL/DIVIDEND/INTEREST/WITHDRAWAL inflows subtract fees and are positive.
- The exact `DIVIDEND REINVESTMENT` note is excluded from external cash flows.
- A one-year -1000/+1100 cash flow produces 10% XIRR, and undefined sign/length cases return `None`.

These tests are a baseline, not proof of parity across every dashboard/report. Before changing formulas, add independently derived fixtures for partial and full sells, multiple fees, leap-year intervals, zero/missing terminal values, MF purchases/redemptions/dividends, multiple sign changes, and the intended semantics of each of the four PWMS XIRR views. Compare outputs only when the scope and terminal-value rules are intended to match. Do not change valuation precedence as part of a release-hardening task.

## 5. Scheduler ownership and duplicate-run prevention

The market-price refresh uses a PostgreSQL advisory lock. The investment-linked AMFI refresh now uses its own PostgreSQL advisory lock around the entire refresh and per-user NAV sync, preventing overlapping refreshes across processes sharing the same PostgreSQL database. SQLite keeps the helper's existing no-op behavior for local development.

Still verify the production topology:

- Record the exact Waitress/WSGI process count and number of application instances.
- Confirm where scheduler startup is invoked and that only one intended job owner is expected, or that every multi-process job has a database-backed lock.
- Confirm that PostgreSQL is the active production database before relying on advisory-lock coordination.
- After restart, inspect the market-data logs for scheduler start/completion messages and AMFI summary fields (`requested_isins`, `matched_isins`, `nav_records`, `unmatched`, `history`).
- Confirm failures are visible and that an interrupted refresh can be safely retried.

Do not start an additional scheduler manually on the production host while the existing application process may already own one.

## 6. Market-price and NAV freshness

A successful HTTP response is not proof that the displayed value is fresh. Verify both persisted observations and the scheduled job logs.

For market prices, run this read-only Django query in the deployed backend environment:

```powershell
python manage.py shell -c "from django.db.models import Max; from market_data.models import MarketPrice; print(list(MarketPrice.objects.values('source').annotate(latest_date=Max('date')).order_by('source')))"
```

Review the latest date by source against the expected trading calendar, allowing for weekends and exchange holidays. Investigate unexpected gaps, repeated provider failures, stale observations, and assets with no market-price history. Manual-price records should remain distinguishable from provider-sourced observations.

For mutual-fund NAVs, use the AMFI refresh summary and the relevant persisted NAV/history models or admin screens to confirm the latest NAV date for invested ISINs and the date coverage required by MIS history. Record unmatched ISINs and investigate them; do not guess mappings by scheme name. Verify that a provider failure does not overwrite a valid stored NAV with a fabricated or zero value.

This repository change does not add a new dashboard/alerting surface for freshness. A follow-up observability change should expose last successful refresh, latest observation date, source, and failure category in a safe operational view.

## 7. Release record

For each release, record:

- Commit SHA and pull request.
- SQLite backend CI, PostgreSQL backend CI, frontend build, and frontend test outcomes.
- Django `check --deploy` warnings and the reviewed production configuration posture (never the secret values).
- Family-isolation and XIRR characterization test outcomes.
- Scheduler ownership, last successful price/NAV refresh, latest observed dates, and unresolved provider/ISIN failures.
- Rollback owner and the command/procedure for restarting the production service.

If a runtime-only check has not been performed, mark it as **not verified** rather than inferring success from source code.
