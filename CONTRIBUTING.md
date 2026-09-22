# Contributing to PWMS

Thank you for your interest in contributing to the Personal Wealth Monitoring System (PWMS).

PWMS is a self-hosted personal and family wealth-management application built with Django, Django REST Framework, Angular, and Python. Contributions should preserve the project's existing architecture, security model, financial-calculation integrity, and data-isolation rules.

## Before You Start

Please read:

- [`README.md`](./README.md)
- [`SETUP.md`](./SETUP.md)
- [`SECURITY.md`](./SECURITY.md)

Before opening an issue or pull request, check whether the topic has already been discussed.

## Repository Structure

```text
backend/     Django + Django REST Framework backend
frontend/    Angular frontend
docs/        Project documentation
README.md    Project overview and architecture
SETUP.md     Installation and setup instructions
```

## Development Requirements

The current project uses:

- Python 3.11+
- Python 3.12 recommended
- Django 5.2
- Django REST Framework
- Node.js 20+
- Angular 21
- SQLite by default
- PostgreSQL as an optional database
- Yahoo Finance for supported market prices
- AMFI for mutual-fund NAV data

Always refer to the current `README.md`, `SETUP.md`, and dependency files for the exact versions used by the repository.

## Creating a Change

Create a feature branch from `main`.

```bash
git checkout main
git pull origin main
git checkout -b feature/your-change
```

Use a descriptive branch name, for example:

```text
feature/watchlist-improvements
fix/xirr-calculation
fix/mutual-fund-import
docs/setup-update
test/portfolio-calculations
```

## Backend Changes

Backend changes should:

- Follow the existing Django application structure.
- Keep business logic on the server where appropriate.
- Preserve existing API contracts unless a breaking change is intentional.
- Use the existing permission and authorization system.
- Validate user and family scope on the backend.
- Avoid trusting role, family, owner, or portfolio identifiers supplied by the client.
- Preserve transaction-based calculations as the source of truth.
- Add or update tests for calculation and authorization changes.

### Important Authorization Rule

PWMS uses backend-enforced role and family permissions.

New endpoints and mutations must use the existing permission architecture. Do not implement authorization only in Angular.

The frontend may hide controls for convenience, but the backend must independently enforce the permission.

## Financial Calculations

Changes involving:

- XIRR
- CAGR
- invested value
- current value
- realized P&L
- unrealized P&L
- holdings
- asset allocation
- historical wealth
- portfolio valuation

must be treated as calculation-sensitive changes.

When modifying financial calculations:

1. Understand the existing calculation flow.
2. Check how transactions are treated.
3. Check how current prices are obtained.
4. Add regression tests where appropriate.
5. Verify that changes do not unintentionally affect other asset classes.
6. Document any intentional calculation change.

Do not hard-code market data, asset classifications, ratios, prices, or other values that are expected to change over time.

## Frontend Changes

Frontend changes should:

- Follow the existing Angular standalone-component architecture.
- Reuse existing services where possible.
- Keep API communication inside the appropriate service layer.
- Preserve the existing RBAC behavior.
- Avoid duplicating backend business logic in the frontend.
- Ensure dark and light modes remain functional where applicable.
- Verify responsive behavior for affected screens.

## Tests

Before opening a pull request, run the relevant tests.

### Backend

From `backend/`:

```bash
python manage.py test
```

For focused testing:

```bash
python manage.py test users portfolio -v 2
```

### Frontend

From `frontend/`:

```bash
npm test
npm run build
```

Run the tests relevant to the changes you make.

## Data and Secrets

Never commit:

- `.env` files containing secrets
- API keys
- passwords
- database files containing real user data
- broker credentials
- demat credentials
- private financial statements
- real transaction exports containing personal information
- access tokens

Use synthetic or anonymized data in examples and tests.

## Issues

When reporting a bug, include:

- What happened.
- What you expected to happen.
- Steps to reproduce it.
- Relevant page or API endpoint.
- Relevant error message or log.
- Whether the issue affects backend, frontend, or both.
- Whether the issue can be reproduced consistently.

Do not include secrets or real personal financial information.

## Pull Requests

A pull request should:

- Have a clear title.
- Explain what changed.
- Explain why the change was necessary.
- Include relevant testing information.
- Mention any database migrations.
- Mention any API changes.
- Mention any frontend changes.
- Include screenshots when UI behavior changes.
- Keep unrelated changes out of the pull request.

Use the pull request template provided by the repository.

## Pull Request Review

Reviewers may request changes relating to:

- Correctness
- Security
- Authorization
- Data isolation
- Financial calculations
- Testing
- Maintainability
- Documentation
- Performance
- Compatibility with existing functionality

Please address review comments before merging.

## Documentation

If a change affects:

- setup
- configuration
- API behavior
- permissions
- calculations
- commands
- architecture
- user-visible functionality

update the appropriate documentation.

## Commit Messages

Use concise commit messages that describe the change.

Examples:

```text
Fix duplicate transaction import
Add family-shared watchlist support
Update portfolio XIRR calculation
Add regression tests for holdings
Fix mutual fund security metadata
Update dashboard allocation logic
```

## Scope of Changes

Prefer focused changes.

Avoid combining unrelated refactors, UI changes, database changes, and bug fixes into a single pull request unless they are directly related.

## Financial Disclaimer

PWMS is a portfolio record-keeping and analytics application. Contributions must not represent the project as providing investment, tax, or legal advice.

---

Thank you for contributing to PWMS.
