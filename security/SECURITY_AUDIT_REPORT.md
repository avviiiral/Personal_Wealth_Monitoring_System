# PWMS Cybersecurity Audit Report

## Scope

Repository: `avviiiral/Personal_Wealth_Monitoring_System`

Audit branch: `security/pwms-cybersecurity-hardening`

Base: `main` at `425fdd0810114ffcff53ecfc1cab52a8702a945f`

Date: 2026-10-16

The review covered the security-relevant Django/DRF backend, Angular authentication and routing, family/portfolio authorization code and tests, PostgreSQL configuration, file-upload paths, AI/news integration, dependency manifests, Git ignore rules, deployment configuration, and repository CI configuration accessible through the GitHub connector. The repository's GitHub code-search index was not available, so an exhaustive generated/static-file inventory and a production infrastructure inspection could not be claimed.

No real user financial data or credentials were used.

## Architecture observed

- Backend: Django 5.2.17 + Django REST Framework 3.18.0.
- Authentication: Django session authentication with browser cookies and CSRF protection.
- Authorization: centralized `users.permissions` role/family authorization, with backend checks intended to derive scope from database state rather than client-supplied role/family IDs.
- Frontend: Angular 21; authentication state is held in memory rather than localStorage/sessionStorage.
- Database: SQLite by default; PostgreSQL supported through Django's PostgreSQL backend.
- AI: optional Gemini HTTPS API for portfolio chat/news analysis; no arbitrary model tool execution was found in the reviewed paths.
- News: Google News RSS plus deterministic local matching/classification; external article text is supplied to Gemini only as analysis data.
- Secrets: environment-based configuration; `.env`, key/certificate files, credentials files, logs, and generated artifacts are ignored by Git.

## Findings

### SEC-001 — Production session/transport security defaults were fail-open
**Severity:** High

**Evidence:** `backend/config/settings.py` previously defaulted `SESSION_COOKIE_SECURE=False`, `CSRF_COOKIE_SECURE=False`, and `SECURE_SSL_REDIRECT=False` even when `DEBUG=False`. HSTS and explicit security response-header settings were also not configured.

**Impact:** A production deployment that omitted one or more environment overrides could expose authentication cookies over HTTP, permit non-TLS requests, and omit HSTS/referrer/COOP/nosniff hardening.

**Remediation implemented:**
- Secure cookie and HTTPS redirect defaults now follow `DEBUG`: production defaults are secure.
- `SESSION_COOKIE_HTTPONLY=True`, `SameSite=Lax`, secure CSRF cookies, `X-Frame-Options: DENY`, nosniff, same-origin referrer policy, same-origin COOP, and HSTS are configured.
- HSTS preload remains explicitly opt-in.
- `backend/.env.example` documents HSTS deployment settings.

**Verification:** Static review completed. Runtime `manage.py check --deploy` could not be executed in this environment.

### SEC-002 — Login endpoint bypassed CSRF protection
**Severity:** High

**Evidence:** `backend/api/views.py::login_view` used `@csrf_exempt`.

**Impact:** The session-authenticated browser application had an unnecessary CSRF exception on the credential-establishing endpoint. While login CSRF has a narrower impact than authenticated state-changing CSRF, removing the exception closes an avoidable cross-site authentication weakness.

**Remediation implemented:**
- Removed `csrf_exempt` from login.
- Angular session login now obtains the CSRF cookie from `/api/health/` and sends `X-CSRFToken`.
- Logout uses the same CSRF mechanism.

**Regression tests added:** `backend/api/tests_security.py`.

### SEC-003 — Login brute-force protection was not enforced at the application layer
**Severity:** High

**Evidence:** `login_view` had no DRF throttle and accepted unlimited credential attempts subject only to external infrastructure.

**Impact:** Credential stuffing and password spraying could consume authentication resources and increase account takeover risk.

**Remediation implemented:**
- DRF `ScopedRateThrottle` is enabled.
- Login scope is `5/minute`.
- Portfolio chat has a separate `20/minute` scope.
- CI documentation notes that reverse-proxy/WAF rate limiting remains desirable because DRF throttling is not a complete DDoS control.

**Regression test:** verifies HTTP 429 after the configured test threshold.

### SEC-004 — AI portfolio chat could disclose internal exception details and had no endpoint throttle
**Severity:** Medium

**Evidence:** `backend/ai/views.py::portfolio_chat` previously returned `str(exc)` when portfolio-context construction failed and had no scoped throttle.

**Impact:** Internal exception details can reveal implementation/database information. An unthrottled authenticated AI endpoint can also create avoidable model/API cost and resource-exhaustion exposure.

**Remediation implemented:**
- Internal exception text is now logged server-side and replaced with a generic client error.
- Portfolio chat is throttled to `20/minute`.

### SEC-005 — External news content required an explicit prompt-injection boundary
**Severity:** Medium

**Evidence:** `backend/portfolio_news/services/gemini_analyzer.py` sends article title/description/source fields from external news providers to Gemini.

**Assessment:** No direct tool execution or arbitrary external action was found in the reviewed analyzer. The existing system prompt already constrained the model to supplied facts and a fixed JSON schema, but it did not explicitly classify article fields as untrusted instructions.

**Remediation implemented:**
- Gemini instructions now explicitly classify all supplied article metadata/content as untrusted external data and instruct the model to ignore commands, URLs, or prompt-like text contained in that data.

### SEC-006 — File-upload total-size controls require further hardening
**Severity:** Medium / residual

**Evidence:** Reviewed Excel upload paths read uploaded workbook bytes directly (for example `users.api_views::tax_rate_upload`). Application-level per-endpoint total file-size limits were not consistently evident.

**Impact:** A large authenticated upload can consume CPU, memory, temporary storage, and workbook-parser resources.

**Status:** Not changed in this branch because multiple import endpoints have different legitimate workbook sizes and a single safe limit needs deployment-specific sizing. Add endpoint-specific byte limits plus reverse-proxy request-size limits before production exposure.

### SEC-007 — PostgreSQL least-privilege role configuration is not verifiable from application source
**Severity:** Medium / deployment risk

**Evidence:** Django supports PostgreSQL through `POSTGRES_USER`/password settings, but no authoritative production PostgreSQL role/GRANT/bootstrap configuration was available in the inspected source.

**Impact:** A deployment could accidentally use a superuser or overly privileged application role.

**Status:** No schema or database role changes were made without evidence of the production topology. Production deployment must use a dedicated application role with only required database privileges; migrations should use a separate controlled migration role where practical.

### SEC-008 — Backup encryption/restore controls are not verifiable
**Severity:** Medium / deployment risk

**Evidence:** No production backup provider, encryption policy, immutable retention configuration, or tested restore procedure was available in the inspected repository.

**Status:** Unresolved and intentionally not claimed as implemented. Validate encrypted backups, access separation, retention, and periodic restore drills outside the application repository.

## Confirmed positive controls

- DRF default permission class is `IsAuthenticated`.
- SessionAuthentication is the configured DRF authentication mechanism.
- Django CSRF middleware is enabled.
- CORS is allowlist-based rather than `CORS_ALLOW_ALL_ORIGINS=True`.
- `SECRET_KEY` is required when `DEBUG=False`.
- Passwords are set through Django's password hashing APIs.
- Password validators are configured and reused for user creation/password changes.
- User management serializers do not expose password hashes.
- Centralized role/family authorization exists in `users.permissions`.
- Existing tests cover privilege escalation, cross-role management, family membership controls, and manual-price ownership.
- Angular authentication state is not persisted in browser localStorage/sessionStorage.
- Gemini uses a fixed Google API endpoint and server-side API key.
- Gemini outputs are parsed against constrained schemas and clamped/validated before use.
- The Git ignore rules exclude environment files, credentials, keys, logs, databases, and generated exports.
- No arbitrary user-controlled URL fetching/tool execution was observed in the reviewed AI/news paths.

## OWASP mapping

| Area | Assessment |
|---|---|
| Broken object-level authorization | Strong existing server-side family/owner controls; regression coverage exists. Continue expanding endpoint-by-endpoint tests. |
| Broken function-level authorization | Strong centralized RBAC; existing tests cover role escalation and management. |
| Authentication failures | Hardened in this branch with CSRF and login throttling. |
| Unrestricted resource consumption | Login/AI throttles added; file-upload sizing and proxy limits remain. |
| Injection | Django ORM/DRF patterns were primarily observed; no confirmed SQL injection found in reviewed paths. |
| Security misconfiguration | Confirmed production transport/header defaults fixed. |
| SSRF | No arbitrary user URL fetch was found in reviewed AI/news code; configured provider URLs still require deployment review. |
| XSS/CSRF | CSRF middleware existed; login CSRF exemption removed; Angular sends CSRF token. CSP remains a deployment-level consideration. |
| Logging/monitoring | Structured rotating logs and audit logs exist; verify production log access/redaction. |
| Supply chain | Security CI added for dependency audits, secret scanning, Django checks, and frontend build. Actual scan results remain pending execution. |

## Secret exposure assessment

No credentials, private keys, or API keys were intentionally added by this branch. The reviewed `.env.example` contains empty placeholders only. A historical secret scan has been added to CI, but its result could not be executed from this environment.

If a real credential is ever found in Git history, rotate it first; deleting it from the latest tree is not sufficient.

## Residual risks

1. Production reverse proxy/WAF configuration is outside the repository and must enforce HTTPS, request-size limits, connection/rate limits, and trusted proxy headers.
2. PostgreSQL role privileges and network ACLs require infrastructure verification.
3. Backup encryption and restore testing require infrastructure access.
4. Full dependency audit results require a runnable networked environment.
5. No production penetration test was performed.
6. CSP for the Angular host should be added after validating the actual production frontend asset/connect requirements.
7. Endpoint-specific file-size limits should be implemented for every workbook/import route.
