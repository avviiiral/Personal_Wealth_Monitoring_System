# PWMS Cybersecurity Remediation Plan

## Completed on this branch

### P0 — Authentication and transport
- Make production session/CSRF cookies secure by default when DEBUG is false.
- Enable HTTPS redirect by default in production.
- Enable HSTS with preload opt-in.
- Enable nosniff, DENY framing, same-origin referrer policy and COOP.
- Remove the login CSRF exemption.
- Update Angular session login/logout to obtain and submit CSRF tokens.
- Add scoped login throttling.

### P1 — AI/resource abuse
- Add a scoped portfolio-chat throttle.
- Stop returning raw backend exception strings from portfolio chat.
- Explicitly treat external news/article fields as untrusted data in Gemini instructions.
- Preserve structured AI output validation.

### P1 — CI/supply chain
- Add a dedicated GitHub Actions security workflow.
- Run Django `check --deploy` and the complete backend test suite.
- Run `pip-audit`.
- Run `npm audit --audit-level=high`.
- Run the Angular production build.
- Run Gitleaks over repository history.
- Pin third-party GitHub Actions to immutable commits.
- Set workflow permissions to read-only unless a future step has a documented need for write access.

## Next remediation wave

### File/resource controls
1. Add explicit byte limits to each upload endpoint.
2. Add reverse-proxy request-body limits.
3. Add parser/workbook row limits where imports permit them.
4. Rate-limit expensive exports and AI/model operations separately from ordinary API traffic.

### PostgreSQL
1. Create a dedicated non-superuser application role.
2. Use a separate migration role if operationally appropriate.
3. Restrict network access to the application host/network.
4. Disable unnecessary extensions and public schema privileges.
5. Review default privileges and future-object grants.
6. Verify family/owner authorization remains application-enforced; PostgreSQL row-level security should only be introduced if its operational model can be demonstrated without breaking existing analytics/reporting.

### Secrets
1. Store production secrets only in the deployment secret manager.
2. Rotate Django SECRET_KEY, Gemini credentials, VAPID private key and database credentials according to the deployment policy.
3. If any secret has ever existed in Git history, revoke/rotate it before history cleanup.
4. Keep secret scanning enabled on every push and pull request.

### CI/CD
1. Keep GitHub Actions pinned to full SHAs.
2. Enable Dependabot/Renovate for Python, npm and GitHub Actions.
3. Review workflow changes as privileged code.
4. Keep `pull_request_target` out of untrusted-code build workflows.
5. Use OIDC/short-lived deployment credentials instead of long-lived cloud keys where supported.
6. Restrict deployment environments and required reviewers.

### Frontend
1. Validate a production CSP against the actual Angular deployment.
2. Keep API origin configuration in environment-specific builds.
3. Do not put secrets or privileged tokens in frontend bundles.
4. Add automated checks for accidental localStorage/sessionStorage credential persistence.

### Monitoring
1. Log authentication failures, privilege changes, family membership changes, exports and sensitive admin actions without recording passwords/tokens.
2. Alert on repeated login failures and abnormal export/AI usage.
3. Retain audit logs separately from application logs when feasible.
4. Define incident-response and credential-revocation procedures.

## Deployment requirements

Before production deployment:
- HTTPS must be fully configured.
- `SESSION_COOKIE_SECURE=True`, `CSRF_COOKIE_SECURE=True`, and `SECURE_SSL_REDIRECT=True` must remain enabled.
- Configure exact `ALLOWED_HOSTS`, `CORS_ALLOWED_ORIGINS`, and `CSRF_TRUSTED_ORIGINS`.
- Configure HSTS only when the domain is HTTPS-only.
- Configure the correct trusted proxy behavior if TLS terminates at a reverse proxy.
- Use a least-privilege PostgreSQL role.
- Configure encrypted backups and a tested restore process.
- Run the security workflow successfully.
- Rotate any credentials suspected of prior exposure.

## Rollback

If the branch causes a functional regression:
1. Stop deployment of the branch.
2. Keep `main` unchanged.
3. Revert/deploy the previous known-good commit.
4. If login fails after CSRF enforcement, verify the frontend is calling `/api/health/`, receiving the CSRF cookie, and sending `X-CSRFToken`.
5. If HTTPS redirects loop behind a proxy, configure `SECURE_PROXY_SSL_HEADER` only after verifying the proxy strips and sets `X-Forwarded-Proto` correctly.
6. Do not disable CSRF, secure cookies, or authorization globally as a workaround.
