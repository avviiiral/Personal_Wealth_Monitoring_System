# Security Hardening Baseline

This document tracks the security controls added to PWMS and the checks still required before describing a deployment as production-ready. No single settings change or automated scan proves an application is secure.

## Application configuration

- With `DEBUG=False`, session cookies and CSRF cookies default to `Secure`, and Django's HTTPS redirect defaults to enabled. Set explicit overrides only when the deployment architecture requires them.
- Session cookies are `HttpOnly` and use `SameSite=Lax`.
- Django SecurityMiddleware is configured to send content-type sniffing protection, a same-origin referrer policy, frame denial, and same-origin opener policy.
- HSTS is intentionally disabled by default. After confirming HTTPS on every relevant hostname, set `SECURE_HSTS_SECONDS=31536000`; only enable `SECURE_HSTS_INCLUDE_SUBDOMAINS` or `SECURE_HSTS_PRELOAD` after verifying that all affected subdomains can remain HTTPS-only.
- `SECURE_PROXY_SSL_HEADER` is safe only when a trusted reverse proxy overwrites the forwarded-protocol header and the app server cannot be reached directly by untrusted clients.
- Keep `SECRET_KEY`, database credentials, push private keys, and provider API keys in deployment secrets/environment variables. Rotate any credential that may have been committed or exposed.

## Required production verification

1. Run `python manage.py check --deploy` with the actual production environment and resolve all applicable warnings.
2. Verify TLS certificates, HTTPS redirects, secure cookies, trusted hostnames, CORS origins, and CSRF trusted origins against the real deployment domains.
3. Verify that the reverse proxy strips and rewrites forwarded headers, limits request sizes, and does not expose the application server directly.
4. Audit every API endpoint for authentication, role checks, object-level authorization, and family/tenant scoping. In particular, test that a user cannot read, modify, export, import, or delete another family's financial records by changing an ID.
5. Review authentication flows, password reset, session invalidation, CSRF protections, brute-force/rate limiting, upload validation, spreadsheet formula injection, SSRF in URL-fetching features, and sensitive-data logging.
6. Run dependency vulnerability scans and update vulnerable packages with regression tests. Review frontend dependencies as well as Python dependencies.
7. Confirm backups are encrypted and restorable, database access is least-privilege, logs have restricted access/retention, and incident-response/secret-rotation procedures are available.

## Scope note

The current baseline improves Django's transport and browser-security defaults. It is not a substitute for a complete endpoint-by-endpoint authorization review, dependency remediation, infrastructure review, or penetration test.
