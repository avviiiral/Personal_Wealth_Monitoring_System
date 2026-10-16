# PWMS Security Test Results

## Execution status

This report distinguishes implemented test coverage from tests actually executed.

**Environment limitation:** The execution environment could not clone the GitHub repository because outbound DNS/network access is unavailable. The GitHub connector also does not expose a workflow-dispatch operation, so the newly added security workflow could not be started from this session.

Therefore, no local test command is claimed as passed and no GitHub Actions run is claimed as passed.

## Tests added

### `backend/api/tests_security.py`

Added regression coverage for:
- login without a CSRF token -> expected HTTP 403;
- login with a CSRF token -> expected HTTP 200 and authenticated session;
- login throttle -> expected HTTP 429 after the configured test threshold.

Existing `backend/users/tests.py` already contains extensive RBAC regression coverage including:
- unauthenticated access denial;
- Viewer privilege restrictions;
- role escalation prevention;
- management-role boundaries;
- family membership restrictions;
- manual-price ownership controls.

## CI checks added

`.github/workflows/security.yml` runs:

1. `python manage.py check --deploy`
2. `python manage.py test`
3. `pip-audit -r backend/requirements.txt`
4. `npm ci`
5. `npm audit --audit-level=high`
6. `npm run build`
7. Gitleaks repository-history secret scanning

All third-party actions are pinned to immutable commit SHAs.

## Tests actually executed in this environment

| Check | Result |
|---|---|
| Repository/branch diff review via GitHub | Completed |
| Security source review | Completed |
| `python manage.py test` | Not executed — repository could not be cloned/network unavailable |
| `python manage.py check --deploy` | Not executed |
| `npm run build` | Not executed |
| `npm audit` | Not executed |
| `pip-audit` | Not executed |
| Gitleaks | Not executed |
| PostgreSQL migration/role verification | Not executed |
| Production penetration test | Not performed |
| Backup restore test | Not performed |

## Static verification performed

- Confirmed login `csrf_exempt` was removed.
- Confirmed login has the `login` throttle scope.
- Confirmed Angular login/logout now obtain and send the CSRF token.
- Confirmed production secure-cookie/HTTPS/HSTS/header settings are present.
- Confirmed portfolio chat has an explicit throttle scope.
- Confirmed raw AI portfolio-context exceptions are no longer returned to the client.
- Confirmed external news content is explicitly treated as untrusted data by the Gemini system instructions.
- Confirmed the security workflow has `permissions: contents: read` and pinned action SHAs.
- Confirmed the branch is 12 commits ahead of `main` and 0 commits behind at review time.

## Required verification after checkout

Run from a clean checkout of the security branch:

```powershell
git fetch origin
git checkout security/pwms-cybersecurity-hardening
git status --short
cd backend
python manage.py check --deploy
python manage.py test
cd ..\frontend
npm ci
npm audit --audit-level=high
npm run build
cd ..\backend
python -m pip install pip-audit
pip-audit -r requirements.txt
```

Then review the GitHub Actions `Security Checks` workflow result before merging.

Use only synthetic test accounts and financial data for manual authorization tests.
