# System Architecture

## High-level architecture

```text
Angular 21 frontend
        |
        | HTTP / WebSocket
        v
Django + Django REST Framework
        |
        +-------------------------------+
        |                               |
   Domain services                 Background workers
        |                               |
        v                               v
 SQLite database                Market data / news refresh
```

The frontend is organized into standalone Angular feature components. Backend responsibilities are split across Django applications rather than being implemented in one monolithic module.

## Backend applications

| App | Main responsibility |
|---|---|
| `api` | Health, authentication, current-user/profile endpoints |
| `users` | Roles, family membership, authorization and audit logs |
| `investments` | Assets, transactions, holdings, SecurityMaster and Excel import |
| `portfolio` | Portfolio tree, portfolio summary, holdings and transactions |
| `analytics` | Wealth, allocation, performance, historical data and XIRR |
| `mutual_funds` | Mutual-fund schemes, NAVs, holdings and SIPs |
| `market_data` | Market prices and price refresh services |
| `ai` | Gemini portfolio chat and usage tracking |
| `portfolio_news` | News retrieval, matching and portfolio alerts |

## Frontend structure

`frontend/src/app` contains application shell/layout/routing. `frontend/src/core` contains shared API clients, authentication/RBAC services and guards. `frontend/src/features` contains page-level features such as Dashboard, Portfolio, Reports, Analytics, Settings, AI Chat and Portfolio News.

The Dashboard consumes separate endpoints for summary, XIRR, investment summary, advisor analytics, portfolio tree and historical wealth. The portfolio tree API is shared by Dashboard and Portfolio screens.

## Portfolio tree flow

```text
Transaction records
        |
        v
PortfolioTreeService
        |
        +--> group by Family
        |      -> Portfolio
        |      -> Asset Class
        |      -> Sub Class
        |      -> Asset
        |
        +--> calculate transaction position
        |
        +--> resolve current price
        |
        +--> calculate current value / P&L / XIRR
        |
        v
JSON tree response
        |
        v
Angular Portfolio / Dashboard
```

The tree service intentionally preserves strategy-specific holdings: the same security may appear in different Family/Portfolio/Asset-Class/Sub-Class combinations without being silently combined.

## Authentication and authorization

PWMS uses Django session authentication with cookies and CSRF protection. Authorization is enforced on the backend through centralized user/family permission logic. Frontend role checks exist to hide inappropriate controls, but backend permission checks remain authoritative.

## Data flow principle

Transactions are the source of truth for positions and invested value. Derived holdings/position data can be rebuilt. Market data supplies current prices, while SecurityMaster supplies descriptive/quantitative security metadata.
