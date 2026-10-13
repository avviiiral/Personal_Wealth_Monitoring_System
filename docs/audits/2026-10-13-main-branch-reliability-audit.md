# PWMS Main-Branch Reliability Audit
Date: 2026-10-13
Repository: https://github.com/avviiiral/Personal_Wealth_Monitoring_System
Baseline branch: main
Baseline commit: 8a07a59dea84996c25fff5b1dab3e483fcb58398

## Scope and safety constraints

This audit plan is based on repository files inspected on the baseline commit. It is not a claim that the entire application or its database has been executed or verified. Runtime tests, PostgreSQL access, and deployment access were not available during this review.

**Financial calculation logic is considered correct and must be preserved.** This workstream must not change XIRR formulas, cash-flow sign conventions, FIFO matching, realized/unrealized P&L formulas, tax classification rules, valuation semantics, or portfolio aggregation behavior. Improvements must be additive: tests, instrumentation, security checks, operational tooling, and performance work that is proven behavior-preserving. Any proposed change that could alter a financial result requires a separate review and explicit approval.

No changes are made to `main`. All work belongs on `audit/pwms-reliability-hardening-20261013`.

## Repository observations

1. Django settings default to SQLite and allow PostgreSQL through `DATABASE_ENGINE`. PostgreSQL connection reuse and health checks are configured.
2. Django REST Framework defaults to authenticated requests with session authentication; CSRF middleware is enabled.
3. `SECRET_KEY` is required when `DEBUG=False`; `ALLOWED_HOSTS` is environment-configurable.
4. Secure cookies and HTTPS redirect default to false. Production deployment must explicitly set appropriate values after confirming TLS termination and reverse-proxy behavior.
5. CORS and CSRF trusted origins are configurable. Production values need deployment-specific verification.
6. MIS API tests cover authentication, date-range validation, historical mutual-fund NAV fallback, report sections, and tax-report fields.
7. Tax settings tests cover save/update/delete history, family visibility, duplicate display names, and rejecting assets from another family.
8. Repository documentation describes external market data sources, background jobs, PostgreSQL as an option, and backup/restore procedures.
9. This review has not established a reproducible production CI gate or measured performance profile. Their presence and status must be verified directly before changing workflows.

## Findings and work items

### P0 — Protect established financial behavior
**Status:** Required invariant, not a reported defect.
- Add or strengthen characterization/regression tests around current XIRR, FIFO, P&L, tax and valuation behavior.
- Use fixed input fixtures and explicit expected outputs from the current implementation.
- Add portfolio/MIS parity checks where both surfaces represent the same metric and date.
- Never rewrite calculations merely to match a new abstraction.
- Validate any optimization against the existing implementation over representative fixtures before adopting it.

Acceptance: all existing and new tests pass; calculations return identical results for the same inputs.

### P1 — Verify PostgreSQL compatibility
- Run Django checks and the full backend test suite against PostgreSQL.
- Validate all migrations on a clean database and a representative upgrade path.
- Test uniqueness constraints, transaction imports, family scoping, and concurrent refreshes.
- Exercise backup and restore against an isolated PostgreSQL instance.

Acceptance: migrations apply cleanly; tests pass; restore produces a usable database; no SQLite-only behavior is assumed in production.

### P1 — Security and family-isolation regression suite
- Enumerate endpoints that read or mutate family-scoped data.
- Test unauthenticated access and cross-family read/write attempts.
- Cover transactions, holdings, tax settings, reports, imports/exports, and notifications where applicable.
- Review upload size/type validation, secrets handling, and sensitive logging.
- Document production settings for HTTPS, secure cookies, allowed hosts, CORS, CSRF, and proxy headers.

Acceptance: cross-family requests are rejected at the API boundary; production settings are explicitly validated.

### P1 — Market-data health and provenance
- Record provider, fetch time, market-data date, success/failure, and error category where this metadata is not already available.
- Identify stale/missing NAVs and prices without substituting invented values or silently turning missing data into zero.
- Add bounded retry/backoff and rate-limit-aware handling where needed.
- Preserve the current valuation source precedence and financial outputs.

Acceptance: a user/operator can distinguish fresh, stale, missing, and failed data; existing valid prices are not overwritten by invalid provider responses.

### P1 — MIS performance baseline
- Measure endpoint latency, database query count, transaction count, valuation lookups, calculation duration, and workbook-generation duration.
- Profile before optimizing.
- Add indexes or bulk loading only after query plans and behavior are validated.
- Treat caching and async report generation as optional follow-ups driven by measurements.
- Do not alter report results or date semantics.

Acceptance: a repeatable benchmark exists and any optimization produces byte-for-byte equivalent key report values for fixed fixtures.

### P1 — Continuous integration and release gates
- Verify existing workflow files before adding or replacing CI configuration.
- Ensure CI installs pinned dependencies, runs Django checks and backend tests, runs Angular tests/build, and validates migrations.
- Add PostgreSQL integration tests where infrastructure is available.
- Add dependency/security checks with actionable failure reporting.

Acceptance: a pull request cannot be considered verified until required checks run successfully; never claim tests passed if they were not run.

### P2 — Observability and background job safety
- Add structured logging and duration/status metrics for external providers, imports, scheduled refreshes, and MIS generation.
- Avoid logging secrets, session tokens, or sensitive financial payloads.
- Detect overlapping refresh jobs and make retries idempotent where the existing operation semantics permit.
- Document job ownership, failure recovery, and manual retry procedures.

Acceptance: operators can diagnose failed jobs and data freshness without inspecting user secrets or relying on silent retries.

### P2 — Frontend reliability and accessibility
- Add tests for loading, empty, stale-data, timeout, and API-error states.
- Test login, family switching, transaction entry, tax settings, MIS date filters, and exports.
- Profile large portfolio screens before introducing lazy loading or state changes.
- Keep existing calculation values and API contracts unchanged.

Acceptance: critical workflows have automated coverage and errors are actionable.

### P2 — News pipeline quality
- Measure ingestion successes/failures, duplicates, source coverage, and holding matches.
- Improve event deduplication and alert explanations only with regression fixtures.
- Keep deterministic scoring semantics unchanged unless a separately approved change specifies new expected behavior.

Acceptance: source and scoring rationale are inspectable; duplicate reduction does not suppress distinct events.

## Required verification report

For each item, record:
- Finding ID and severity.
- File/path and exact behavior.
- Reproduction or test.
- Change made.
- Test command and actual result.
- Financial-output parity evidence, where applicable.
- Remaining risks and rollback approach.

## Release rule

Do not merge this branch until the CI checks pass, PostgreSQL verification is complete, security regression tests pass, and financial outputs are shown unchanged. If environment access is unavailable, mark the item **not verified**, not complete.
