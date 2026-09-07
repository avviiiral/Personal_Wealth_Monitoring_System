# PWMS Main Branch Project Overview

This document is a business-oriented overview of the current Personal Wealth Monitoring System (PWMS) implemented on the `main` branch.

## 1. What the product does

PWMS brings investment information into one place so a user can understand what is owned, what it is worth, how it has performed, and how wealth is distributed.

The application supports investment activity and analysis across stocks, ETFs, mutual funds, SIPs, bonds, gold, cash, real estate and other asset categories. The backend is responsible for authoritative financial calculations; the Angular frontend presents the results.

## 2. The system in one flow

`Transactions + market/NAV data -> calculated holdings -> unified wealth analytics -> dashboard / portfolio / reports -> optional AI explanation`

A user action starts in the frontend. Angular calls a Django API. Django reads application data, applies business rules and calculations, and returns structured results. Analytics services consolidate those results before the frontend renders cards, tables and charts.

## 3. Major components

- **Angular frontend:** screens, forms, navigation and charts.
- **Django + Django REST Framework backend:** APIs, authentication, business rules and financial calculations.
- **SQLite database:** persistent application state.
- **Market-data services:** prices/NAVs and valuation refresh workflows.
- **Analytics services:** unified wealth, allocation, performance, historical wealth, investment summary and XIRR.
- **AI portfolio assistant:** conversational interpretation of verified portfolio context.

## 4. Core investment model

An `Asset` represents an investment instrument. A `Transaction` records financial activity such as buy, sell, SIP, dividend, interest, deposit or withdrawal. A `Holding` is the calculated current position. `PortfolioPosition` supports family/portfolio-specific positions.

The transaction model also preserves the business hierarchy used by the source data: Family Name -> Asset Class -> Sub Class -> Asset Name -> Underlying -> Advisors.

## 5. Mutual funds and SIPs

Mutual funds have dedicated scheme, NAV, transaction and holding records. SIPs are recurring investment instructions with individual installments. Installments can be scheduled, due, executed, skipped or failed. Execution creates the relevant mutual-fund transaction and updates the holding.

## 6. Market data

The current market-data manager supports:

- Stocks / ETFs through Yahoo Finance.
- Mutual-fund NAVs through AMFI.
- Bonds through NSE CBRICS data.
- Sovereign Gold Bonds through a dedicated NSE-based price path.

The fetched values are stored in the application database and then used to rebuild holdings.

## 7. Unified wealth analytics

Unified wealth analytics combines investment and mutual-fund holdings and calculates invested value, current value and P&L measures. It acts as an aggregation layer rather than replacing the underlying holding engines.

XIRR is calculated from dated cash flows. The implementation uses Newton-Raphson with a bisection fallback.

## 8. Investment Summary

The Investment Summary creates a business-friendly classification of current value:

| Asset Category | Asset Classes |
|---|---|
| Other | Unlisted |
| Alternate | Commodity, Private Equity, REITs, InvITs |
| Equities | Direct Equity, Equity PMS, Equity AIF, Equity Mutual Fund, Equity LRS |
| Fixed Income | Debt Mutual Fund, Gold Bond |
| Liquids | Liquid Mutual Fund, Arbitrage Mutual Fund |

The service preserves raw classifications used to derive each displayed class and keeps unmatched values under Other / Unlisted instead of dropping them.

## 9. AI assistant

The AI layer receives a backend-generated portfolio context containing summary, allocation, performance, holdings, recent transactions and SIP information. The AI is an interpretation layer; it is not the authoritative source for portfolio numbers.

## 10. Product areas

- Dashboard
- Portfolio
- Holdings
- Mutual Funds
- SIPs
- Analytics
- Settings
- AI Chat

## 11. Technology stack

Frontend: Angular 21, TypeScript, RxJS, Chart.js / ng2-charts, Angular CDK, optional Angular SSR / Express.

Backend: Python 3.11, Django 5.2, Django REST Framework, pandas, NumPy, yfinance, requests / BeautifulSoup and SQLite.

External sources named by the project: Yahoo Finance, AMFI and OpenAI.

## 12. Current-state dependency

The current README documents a local `backend/data/transactions.xlsx` file used by the portfolio-tree synchronization. The file is intentionally not committed because it contains personal financial data. The current application therefore combines committed code with a locally supplied transaction dataset.

## 13. End-to-end example

A stock purchase is recorded as a transaction. A market-data service obtains a current price. The holding engine rebuilds the position. Unified wealth analytics includes the position in portfolio totals. Investment Summary classifies it into the business hierarchy. The frontend displays the result, and the AI assistant can later explain it using the verified backend context.

## 14. Current-state conclusion

The main branch is organized as a layered wealth-management application: financial activity is captured in transactions, valuation inputs come from market/reference data, holdings are calculated, analytics consolidates the results, the frontend presents them, and the AI layer explains the verified information.
