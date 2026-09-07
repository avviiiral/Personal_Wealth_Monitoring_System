# PWMS Wiki

## Personal Wealth Monitoring System

PWMS is a self-hosted personal and family wealth management application for Indian investors. It brings equities, ETFs, bonds, Sovereign Gold Bonds, mutual funds and SIPs into one system with transaction-driven portfolio calculations, market-data updates, family sharing, role-based access control, reports and AI-assisted portfolio/news features.

## Start here

- [System Architecture](Architecture.md)
- [Roles & Family Access](RBAC.md)
- [Portfolio Calculations](Portfolio-Calculations.md)
- [Market Data & Mutual Funds](Data-Sources.md)
- [AI Chat & Portfolio News](AI-News.md)
- [Deployment & Operations](Deployment.md)
- [Troubleshooting](Troubleshooting.md)

## Main application areas

| Area | Purpose |
|---|---|
| Dashboard | Wealth overview, allocation, investment summary and historical wealth |
| Portfolio | Hierarchical holdings, transaction details, XIRR, manual prices |
| Analytics | Allocation, performance, historical wealth and XIRR analytics |
| Mutual Funds & SIPs | Scheme/NAV tracking, holdings and SIP management |
| Reports | Excel/PDF portfolio and transaction exports |
| Settings | Profile, users, families and manual-price administration |
| AI Chat | Portfolio-aware Gemini assistant |
| Portfolio News | Holding-aware news discovery and alerting |

## Technology

- Backend: Django 5.2 + Django REST Framework
- Frontend: Angular 21 + TypeScript
- Database: SQLite by default
- Charts: Chart.js / ng2-charts
- Market data: Yahoo Finance and AMFI
- News: Google News RSS
- AI: Google Gemini REST API
- Background work: in-process Python threads

For exact dependency versions and repository structure, see `README.md` and `backend/requirements.txt`.
