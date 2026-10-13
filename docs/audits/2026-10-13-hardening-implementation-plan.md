# PWMS Reliability Hardening — Implementation Plan
Base: `main` at `8a07a59dea84996c25fff5b1dab3e483fcb58398`
Work branch: `audit/pwms-reliability-hardening-20261013`

## Non-negotiable constraint

Existing financial calculation logic is correct. Preserve it. Do not change formulas, matching order, cash-flow conventions, date semantics, tax classifications, price selection precedence, or aggregation behavior. The objective is to improve assurance and operational reliability around the existing implementation.

## Sequence

1. **Baseline and inventory**
   - Confirm branch base and working tree.
   - Inventory existing tests, CI workflows, settings, migrations, and scheduled tasks.
   - Run current test/build commands and capture actual outcomes.

2. **Characterization tests**
   - Add missing regression tests without changing production calculation code.
   - Include XIRR, FIFO, tax settings, portfolio/MIS parity, date boundaries, and family-member isolation.
   - Derive expected results from reviewed fixtures and independently verified examples.

3. **PostgreSQL verification**
   - Run checks/migrations/tests against PostgreSQL.
   - Fix only verified compatibility issues that do not change calculation semantics.
   - Test clean install and upgrade paths.

4. **Security**
   - Audit all family-scoped endpoints.
   - Add cross-family negative tests.
   - Document and validate production HTTPS/cookie/CSRF/CORS/host configuration.
   - Do not change local development defaults without a migration plan.

5. **Market-data reliability**
   - Add observability and freshness metadata as needed.
   - Keep current source precedence and valuation behavior.
   - Test provider errors, rate limits, stale records, and partial data.

6. **MIS performance**
   - Capture baseline latency/query counts on representative fixtures.
   - Profile bottlenecks.
   - Optimize only with result-parity tests and measured evidence.

7. **CI and release checks**
   - Inspect existing workflows first.
   - Ensure backend checks/tests, PostgreSQL integration, Angular tests/build, migration checks, and dependency checks run.
   - Report actual CI results; do not infer success from configuration alone.

8. **Frontend and news pipeline**
   - Improve error/loading/accessibility behavior and observability without changing financial values or news scoring semantics.

## Merge criteria

- Existing financial logic untouched unless a separate explicit approval is granted.
- Regression tests pass.
- PostgreSQL migrations and integration tests pass.
- Cross-family authorization tests pass.
- Angular tests and production build pass.
- Market-data and MIS changes demonstrate output parity.
- No secrets or sensitive financial payloads leak into logs.
- All unverified items are clearly disclosed.
