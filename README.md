<div align="center">

# 💰 PWMS — Personal Wealth Monitoring System

**A self-hosted personal & family wealth tracker for Indian investors** — stocks, ETFs,
bonds, SGBs, mutual funds and SIPs in one place, with real XIRR, live prices, role-based
family sharing, and an AI-assisted news layer on top.

[![Django](https://img.shields.io/badge/Django-5.2-092E20?logo=django&logoColor=white)](https://www.djangoproject.com/)
[![Django REST Framework](https://img.shields.io/badge/DRF-3.18-A30000?logo=django&logoColor=white)](https://www.django-rest-framework.org/)
[![Angular](https://img.shields.io/badge/Angular-21-DD0031?logo=angular&logoColor=white)](https://angular.dev/)
[![Python](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![TypeScript](https://img.shields.io/badge/TypeScript-Angular-3178C6?logo=typescript&logoColor=white)](https://www.typescriptlang.org/)
[![License](https://img.shields.io/badge/License-Proprietary-red.svg)](License.md)

[Setup Guide](SETUP.md) · [Feature Tour](#feature-tour) · [API Reference](#api-reference) · [Tech Stack](#tech-stack)

</div>

---

## What is PWMS?

PWMS is a full-stack **personal and family wealth management system** built for
investors who hold **Indian equities, ETFs, bonds, Sovereign Gold Bonds, mutual funds
and SIPs** and want one place that actually computes the numbers instead of estimating
them. Every figure — holdings, invested value, current value, unrealized/realized P&L,
**XIRR**, CAGR, and asset allocation — is calculated server-side from real transactions,
kept current with automatic background price refreshes (Yahoo Finance for
stocks/ETFs, AMFI for mutual fund NAVs), and never invented by the AI layer sitting on
top of it.

It's built for households, not just individuals: a **four-tier role hierarchy** (System
Owner / Super User / Admin / Viewer) and **many-to-many family membership** let several
people share visibility into the same portfolio — or several portfolios — with
permissions enforced independently on the backend, not just hidden in the UI.

For step-by-step install instructions on a new machine, see **[`SETUP.md`](SETUP.md)**.
This file is the knowledge base of what the software does and how it's built.

---

## ✨ Feature tour

### 📊 Dashboard
Net worth, asset allocation, key portfolio metrics, and an Investment Summary table
broken down by asset class — computed server-side, scoped to the user's own data plus
their currently active family's data (or everyone's, for a System Owner).

### 📁 Portfolio
A hierarchical tree of holdings (Family → Portfolio → Asset Class → Sub-Class → Asset)
with quantity, invested value, current value, P&L and XIRR per node, full transaction
history, and — for Admin and above — an inline **manual price override** available for
any asset within the user's visible family scope.

### 📈 Analytics
Wealth allocation by asset class, sector, market cap, AMC and advisor; performance
ranking; XIRR; historical wealth over any date range; dedicated equity and
fixed-income breakdowns.

### 🏦 Mutual Funds & SIPs
Scheme holdings, NAV-based valuation, SIP creation, due/overdue tracking, and
per-installment SIP execution.

### 📄 Reports
Export transactions, holdings and portfolio summaries to **Excel or PDF**, matching
exactly what the Portfolio and Dashboard pages show.

### 🔐 Settings
Account & preferences, role-scoped **User Management**, System-Owner-only **Family
Management**, and a **Manual Prices** screen for overriding any asset's price within
your visible family scope — every override is audit-logged (who, when, from what).

### 🤖 AI Portfolio Chat
A Gemini-backed assistant scoped to the logged-in user's own portfolio. The backend
builds a structured context (holdings, allocation, recent performance) and hands it to
Gemini — Gemini interprets the numbers it's given, it never computes or invents them.

### 📰 Portfolio News Intelligence
A background agent that reads each user's *actual* holdings (no hard-coded stock
list), searches Google News RSS, deterministically matches articles to holdings before
spending an AI call on any of them, scores each alert by
`impact × portfolio_weight × confidence`, and raises a browser notification for
high-impact items — fully automatic, no scheduled task to configure.

---

## 🔑 Roles, permissions & family membership

Four hierarchical roles, enforced on every request by a centralized permission service
— the frontend hides controls for convenience, but the backend independently blocks
unauthorized requests regardless of what the UI shows.

```
VIEWER  <  ADMIN  <  SUPER_USER  <  SYSTEM_OWNER
```

Role and family membership are deliberately **separate concepts**: role determines
what a user can *do*; family membership determines *whose data* they can *see*.

| Capability                                              | Viewer | Admin | Super User | System Owner |
| -------------------------------------------------------- | :----: | :---: | :--------: | :----------: |
| View Dashboard / Portfolio / Analytics / AI Chat / News    |   ✅   |  ✅   |     ✅     |      ✅      |
| Edit manual prices (family-shared)                         |   ❌   |  ✅   |     ✅     |      ✅      |
| Create a Viewer                                             |   ❌   |  ✅   |     ✅     |      ✅      |
| Create an Admin                                             |   ❌   |  ❌   |     ✅     |      ✅      |
| Create a Super User / System Owner                          |   ❌   |  ❌   |     ❌     |      ✅      |
| Create / manage families                                    |   ❌   |  ❌   |     ❌     |      ✅      |
| View every family's data                                    |   ❌   |  ❌   |     ❌     |      ✅      |

A user can belong to **zero, one, or many** families at once, with a personal
**active-family selector** scoping every data screen to one family at a time — a
System Owner is the one exception, always seeing every family's data regardless of
selection. See [`users/permissions.py`](backend/users/permissions.py) for the full
authorization logic — nothing here trusts a role or family ID the client claims; every
check re-derives it from the database on every request.

---

## 🛠 Tech stack

| Layer                  | Technology                                                                                    |
| ------------------------ | ----------------------------------------------------------------------------------------------- |
| Backend framework        | Django 5.2 + Django REST Framework 3.18                                                        |
| Backend language         | Python 3.12                                                                                     |
| Database (default)       | SQLite (WAL journal mode + busy-timeout for background-scheduler concurrency)                   |
| Production serving       | `runserver` for dev; **waitress** (WSGI) or **uvicorn** (ASGI) for production                    |
| Auth                     | Django session authentication (cookie + CSRF)                                                  |
| Frontend framework       | Angular 21 (standalone components, no NgModules)                                               |
| Frontend language        | TypeScript                                                                                      |
| Charts                   | Chart.js / ng2-charts                                                                           |
| Excel import/export      | `openpyxl` (backend), `exceljs` (frontend)                                                     |
| PDF export                | `jspdf` + `jspdf-autotable` (frontend)                                                          |
| Market data                | Yahoo Finance (`yfinance` + `curl_cffi`), AMFI NAV feed over HTTP                              |
| News retrieval             | Google News RSS via `feedparser` — no paid news API                                            |
| AI                          | Google Gemini REST API                                                                        |
| Background scheduling       | In-process Python threads (market prices, daily refresh, post-import refresh, portfolio news) — no Celery/Redis |
| Logging                     | Centralized rotating file handler (`backend/logs/pwms.log`)                                    |

---

## 📂 Repository structure

```text
Personal_Wealth_Monitoring/
├── backend/
│   ├── manage.py
│   ├── requirements.txt
│   ├── .env.example           Template for every deployment-sensitive setting
│   ├── config/                 Settings (env-driven SECRET_KEY/DEBUG/ALLOWED_HOSTS/
│   │                           CORS/logging), scheduler_guard.py, root urls.py, WSGI/ASGI
│   ├── api/                    Health check, login/logout, profile settings
│   ├── users/                  RBAC: 4-tier roles, FamilyGroup, FamilyMembership,
│   │                           UserAuditLog, centralized permissions.py
│   ├── investments/            Asset, Transaction, Holding, SecurityMaster,
│   │                           Excel transaction import, AMC-name/quant enrichment
│   ├── market_data/             MarketPrice, price providers, background scheduler
│   ├── portfolio/               Portfolio tree/summary/holdings/transactions APIs
│   ├── mutual_funds/             Schemes, NAVs, MF transactions, SIPs, MF holdings
│   ├── analytics/                Wealth/allocation/performance/XIRR analytics
│   ├── ai/                       Gemini portfolio chat, Gemini usage tracking
│   ├── portfolio_news/           News discovery, matching, dedup, alert scoring
│   └── data/
│       ├── security_master.xlsx           Reference security data
│       └── security_master_lookups.json   Researched sector/AMC/ratio data
│
├── frontend/
│   ├── package.json / angular.json
│   └── src/
│       ├── environments/       environment.ts (dev) / environment.prod.ts (build-time
│       │                       swap via angular.json's fileReplacements) — the ONE
│       │                       place the backend API URL is set
│       ├── app/                 Shell, layout (sidebar/header incl. family switcher), routing
│       ├── core/
│       │   ├── services/        One API client per backend area + RBAC service
│       │   └── guards/          auth.guard (also loads the RBAC role)
│       ├── features/
│       │   ├── dashboard/  portfolio/  analytics/  reports/
│       │   ├── ai-chat/  portfolio-news/  login/
│       │   └── settings/
│       │       ├── user-management/
│       │       ├── family-management/
│       │       └── manual-prices/
│       └── shared/
│
├── README.md                   This file
├── SETUP.md                    Step-by-step install guide
└── License.md
```

---

## 🏗 Backend architecture

Each Django app owns one area of the domain, mounted under a fixed prefix in
`backend/config/urls.py`:

| App              | Prefix                     | Responsibility                                                        |
| ----------------- | ---------------------------- | ----------------------------------------------------------------------- |
| `api`             | `/api/`                     | Health check, login/logout/current-user, profile settings              |
| `users`           | `/api/settings/`             | RBAC, user management, family management                               |
| `portfolio`       | `/api/portfolio/`            | Assets, transactions, portfolio tree/summary/holdings, manual price edit |
| `analytics`       | `/api/analytics/`            | Wealth allocation, performance, XIRR, historical analytics              |
| `mutual_funds`    | `/api/mutual-funds/`         | Schemes, MF transactions, MF holdings, SIPs                             |
| `market_data`     | (internal + stock search)     | Price providers, background price refresh                             |
| `ai`              | `/api/ai/`                   | Portfolio chat; also mounts `portfolio_news`'s URLs                     |
| `investments`     | `/api/investments/`          | Excel transaction import, Security Master                              |

`users.permissions` is the single centralized authorization service — role-rank
helpers, family-scope helpers (`get_visible_owner_ids`, `get_manageable_users_queryset`),
and reusable DRF permission classes. Nothing trusts a role or family ID the client
claims; every check re-derives it from the database on every request.

---

## 🗃 Data model overview

**`users`** — `UserProfile` (role, family memberships, active family), `FamilyGroup`,
`FamilyMembership`, `UserAuditLog` (append-only audit trail).

**`investments`** — `Asset`, `Transaction` (source of truth for quantity/invested
value), `Holding` (derived, rebuildable), `SecurityMaster` (sector, cap-type, AMC name,
P/E, P/B, ROE, credit rating).

**`market_data`** — `MarketPrice` (tagged by source: Yahoo Finance, AMFI, or Manual).

**`mutual_funds`** — `MutualFundScheme`, `MutualFundNAV`, `MutualFundTransaction`,
`MutualFundHolding`, `SIP`, `SIPInstallment`.

**`portfolio_news`** — `NewsArticle`, `NewsArticleSource`, `PortfolioNewsAlert`
(unique per user/article/holding).

**`ai`** — `GeminiUsageLog` (token usage per Gemini call, chat and news agent alike).

---

## 🔌 API reference

All endpoints require an authenticated Django session unless noted.

<details>
<summary><strong>Auth & profile — <code>/api/</code></strong></summary>

```
GET  /api/health/
POST /api/auth/login/
POST /api/auth/logout/
GET  /api/auth/me/
GET  /api/settings/
POST /api/settings/update/
POST /api/settings/change-password/
```
</details>

<details>
<summary><strong>RBAC / Users / Families — <code>/api/settings/</code></strong></summary>

```
GET   /api/settings/me/
POST  /api/settings/me/active-family/
GET   /api/settings/users/
POST  /api/settings/users/
GET/PUT/PATCH/DELETE /api/settings/users/<id>/
POST  /api/settings/users/<id>/activate/
POST  /api/settings/users/<id>/deactivate/
POST  /api/settings/users/<id>/reset-password/
GET/POST /api/settings/groups/
PATCH/DELETE /api/settings/groups/<id>/
POST/DELETE  /api/settings/groups/<id>/members/[<user_id>/]
GET   /api/settings/prices/
PUT/PATCH/DELETE /api/settings/prices/<asset_id>/
```
</details>

<details>
<summary><strong>Portfolio — <code>/api/portfolio/</code></strong></summary>

```
GET/POST   /api/portfolio/assets/
GET/PUT/PATCH/DELETE /api/portfolio/assets/<id>/
GET/POST   /api/portfolio/transactions/
GET/PUT/PATCH/DELETE /api/portfolio/transactions/<id>/
GET        /api/portfolio/summary/
GET        /api/portfolio/holdings/
GET        /api/portfolio/tree/
PUT/PATCH/DELETE /api/portfolio/assets/<id>/manual-price/
```
</details>

<details>
<summary><strong>Analytics — <code>/api/analytics/</code></strong></summary>

```
GET /api/analytics/wealth/summary/
GET /api/analytics/wealth/allocation/
GET /api/analytics/wealth/performance/
GET /api/analytics/wealth/xirr/
GET /api/analytics/wealth/investment-summary/
GET /api/analytics/wealth/sector-allocation/
GET /api/analytics/wealth/market-cap-allocation/
GET /api/analytics/wealth/equity-analysis/
GET /api/analytics/wealth/fixed-income-analysis/
GET /api/analytics/wealth/historical/
```
</details>

<details>
<summary><strong>Mutual Funds & SIPs — <code>/api/mutual-funds/</code></strong></summary>

```
GET  /api/mutual-funds/summary/
GET  /api/mutual-funds/holdings/
GET  /api/mutual-funds/transactions/
POST /api/mutual-funds/transactions/create/
GET  /api/mutual-funds/schemes/
GET  /api/mutual-funds/sips/
GET  /api/mutual-funds/sips/due/
POST /api/mutual-funds/sips/create/
POST /api/mutual-funds/sip-installments/<id>/execute/
```
</details>

<details>
<summary><strong>Investments & AI — <code>/api/investments/</code>, <code>/api/ai/</code></strong></summary>

```
POST /api/investments/import/
GET  /api/investments/security-master/
GET/PATCH /api/investments/security-master/<id>/

POST /api/ai/chat/
GET  /api/ai/news/
GET  /api/ai/notifications/
POST /api/ai/notifications/<alert_id>/read/
```
</details>

---

## 🖥 Frontend architecture

- **Standalone Angular components** throughout — no `NgModule`s.
- `core/services/rbac.service.ts` is the single source of role/permission/family state
  — used to hide controls, but every action is still independently authorized by the
  backend.
- One dedicated API client service per backend area under `core/services/`, each
  reading its base URL from `environment.apiUrl` (see `src/environments/`) — nothing
  hardcodes a host.
- `core/services/browser-notification.service.ts` shows native browser notifications
  for new Critical/High news alerts, polled every 60 seconds.
- Routes sit under a `ShellComponent` behind `authGuard`: `/dashboard`, `/portfolio`,
  `/reports`, `/analytics`, `/settings`, `/ai-chat`, `/portfolio-news`.

---

## ⏱ Automated jobs / schedulers

Four independent in-process mechanisms, all started automatically from each app's
`AppConfig.ready()` — deliberately not using Celery/Redis, and correctly detecting
whether they're running under `runserver`, **waitress**, or **uvicorn** so they start
exactly once regardless of how the server is launched (see
[`config/scheduler_guard.py`](backend/config/scheduler_guard.py)):

1. **Market price refresh** — every 15 minutes, Stock/ETF prices (Yahoo Finance) and
   mutual fund NAVs (AMFI).
2. **Daily refresh** — once per calendar day of uptime: AMFI NAV, security master
   ratios, SIP sync/execute.
3. **Post-import price refresh** — fires right after a transaction import commits, so
   a newly added asset shows a live price immediately.
4. **Portfolio News monitoring** — fully automatic, same interval-controlled pattern
   as the others (`NEWS_MONITOR_INTERVAL`, default 30 minutes). No external scheduler,
   no scheduled task, no `.bat` file to configure — it runs for as long as the server
   process is up.

---

## 🔧 Environment variables

Loaded from `backend/.env` (see [`.env.example`](backend/.env.example) for the full
template) — every value has a dev-safe default, so local development works with no
`.env` file at all.

| Variable                  | Purpose                                        | Dev default              |
| --------------------------- | ------------------------------------------------- | -------------------------- |
| `SECRET_KEY`                 | Django's cryptographic signing key                | insecure placeholder      |
| `DEBUG`                      | Debug mode                                        | `True`                    |
| `ALLOWED_HOSTS`               | Comma-separated allowed hosts                     | *(empty)*                 |
| `CORS_ALLOWED_ORIGINS`        | Comma-separated allowed frontend origins           | `http://localhost:4200`   |
| `CSRF_TRUSTED_ORIGINS`        | Comma-separated trusted origins for CSRF           | `http://localhost:4200`   |
| `SESSION_COOKIE_SECURE`       | Require HTTPS for the session cookie               | `False`                   |
| `CSRF_COOKIE_SECURE`          | Require HTTPS for the CSRF cookie                  | `False`                   |
| `SECURE_SSL_REDIRECT`         | Force-redirect HTTP → HTTPS                        | `False`                   |
| `GEMINI_API_KEY`              | AI Chat, Portfolio News analysis (or `GOOGLE_API_KEY`) | —                     |
| `NEWS_MONITOR_INTERVAL`       | Seconds between automatic news monitor runs        | `1800` (30 min)           |

---

## 📜 Management commands

Run any of these from `backend/` with the virtual environment active:
`python manage.py <command>`.

| Command                        | App              | What it does                                                          |
| --------------------------------| -----------------| -------------------------------------------------------------------------|
| `monitor_portfolio_news`        | `portfolio_news` | One full news-monitoring pass for every user (also runs automatically)   |
| `gemini_usage`                   | `ai`             | Prints a summary of Gemini token usage                                  |
| `link_security_master`           | `investments`    | Link Assets to their SecurityMaster row by ISIN (dry-run by default)     |
| `load_security_master_data`      | `investments`    | Load researched sector/cap-type/P-E/P-B/ROE data into SecurityMaster     |
| `import_amfi_cap_classification` | `investments`    | Classify stocks Large/Mid/Small Cap by AMFI rank (dry-run by default)    |
| `fetch_amfi_nav`                 | `mutual_funds`   | Download/import the current AMFI NAV file (batched commits)              |
| `execute_sips`                   | `mutual_funds`   | Execute all due SIP installments for a user                             |
| `rebuild_holdings`               | `portfolio`      | Rebuild portfolio holdings from transactions for a user                  |

See [`SETUP.md`](SETUP.md) for the commands you'll actually run during first-time setup.

---

## ✅ Testing

Backend: `cd backend && python manage.py test` (or target one app, e.g.
`python manage.py test users portfolio -v 2`). `users/tests.py` covers every
role × capability combination and privilege-escalation attempt; `mutual_funds/tests.py`
covers batched AMFI NAV import; `investments/tests.py` covers the transaction importer
and AMC-name/quant auto-enrichment.

Frontend: `cd frontend && npm test` (unit tests), `npm run build` (verifies the whole
app compiles).

---

## ⚠️ Known limitations

- **SQLite** is the default database — WAL mode + busy-timeout reduce (not eliminate)
  write contention under concurrent load, and there's no automated backup strategy yet.
  Fine for a household; a real production deployment with many concurrent writers
  should move to Postgres.
- The four background schedulers assume **exactly one running server process** — if
  ever deployed behind multiple worker processes (not threads), each process would
  start its own independent copy of every scheduler.
- `Asset`/`Transaction` are still stored against a single owning `User` account;
  family-shared access is layered on top via the authorization functions in
  `users/permissions.py`, not a change to the underlying ownership field.
- Portfolio News notifications are **browser polling** (every 60s), not real push —
  they only fire while the Angular app is open.

See [`SETUP.md`](SETUP.md) for what to configure before any real deployment
(`.env`, `environment.prod.ts`, and the WSGI/ASGI serving options).

---

## 📄 License

See [`License.md`](License.md).
