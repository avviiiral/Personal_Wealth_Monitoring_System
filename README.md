<div align="center">

<img src="https://capsule-render.vercel.app/api?type=waving&color=0:0f2027,50:203a43,100:2c5364&height=200&section=header&text=PWMS&fontSize=72&fontColor=ffffff&fontAlignY=38&desc=Personal%20Wealth%20Monitoring%20System&descSize=22&descAlignY=60" alt="PWMS - Personal Wealth Monitoring System" width="100%" />

# 💰 Personal Wealth Monitoring System

### Your family's entire portfolio. Real numbers. One place.

**A self-hosted wealth tracker for Indian investors** — stocks, ETFs, bonds, Sovereign Gold Bonds, mutual funds and SIPs — with true **XIRR**, live prices, role-based family sharing, and a deterministic portfolio-news layer that works without any paid AI API.

![Django](https://img.shields.io/badge/Django-5.2-092E20?style=for-the-badge&logo=django&logoColor=white)
![DRF](https://img.shields.io/badge/DRF-3.18-A30000?style=for-the-badge&logo=django&logoColor=white)
![Angular](https://img.shields.io/badge/Angular-21-DD0031?style=for-the-badge&logo=angular&logoColor=white)
![Python](https://img.shields.io/badge/Python-3.12-3776AB?style=for-the-badge&logo=python&logoColor=white)
![TypeScript](https://img.shields.io/badge/TypeScript-Angular-3178C6?style=for-the-badge&logo=typescript&logoColor=white)
![SQLite](https://img.shields.io/badge/SQLite-default-003B57?style=for-the-badge&logo=sqlite&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-optional-4169E1?style=for-the-badge&logo=postgresql&logoColor=white)
![License](https://img.shields.io/badge/License-Proprietary-red?style=for-the-badge)

[🚀 Quick start](#-quick-start) &nbsp;·&nbsp; [✨ Features](#-features) &nbsp;·&nbsp; [🧭 How it works](#-how-it-works) &nbsp;·&nbsp; [🔑 Roles](#-roles-and-permissions) &nbsp;·&nbsp; [🔌 API](#-api-reference) &nbsp;·&nbsp; [📘 Setup guide](./SETUP.md)

</div>

---

## 📑 Table of contents

|                                                     |                                                   |                                                                            |
| --------------------------------------------------- | ------------------------------------------------- | -------------------------------------------------------------------------- |
| [🌟 Overview](#-overview)                           | [🧰 Tech stack](#-tech-stack)                     | [🔧 Configuration](#-configuration)                                        |
| [🚀 Quick start](#-quick-start)                     | [📂 Repository structure](#-repository-structure) | [📜 Management commands](#-management-commands)                            |
| [✨ Features](#-features)                           | [🧱 Backend architecture](#-backend-architecture) | [🔒 Security model and privacy](#-security-model-and-privacy)              |
| [🧭 How it works](#-how-it-works)                   | [🧬 Data model](#-data-model)                     | [✅ Testing](#-testing)                                                    |
| [🔑 Roles and permissions](#-roles-and-permissions) | [🔌 API reference](#-api-reference)               | [🚧 Known limitations](#-known-limitations)                                |
| [🎨 Frontend architecture](#-frontend-architecture) | [⏰ Background jobs](#-background-jobs)           | [🤝 Contributing](#-contributing) · [📄 License](#-license-and-disclaimer) |

---

## 🌟 Overview

**PWMS** is a full-stack **personal and family wealth management system** for investors who hold **Indian equities, ETFs, bonds, Sovereign Gold Bonds (SGBs), mutual funds and SIPs** and want one place that _computes_ the numbers instead of estimating them.

Every figure you see — holdings, invested value, current value, unrealized and realized P&L, **XIRR**, **CAGR** and asset allocation — is calculated **server-side from your real transactions**. Prices stay fresh through automatic background refreshes (**Yahoo Finance** for stocks and ETFs, **AMFI** for mutual-fund NAVs). Historical MIS valuations use persisted market/NAV history prepared by background jobs, so the interactive report does not perform live provider downloads. Mutual-fund history supports both the dedicated mutual-fund transaction models and legacy/imported mutual-fund transactions stored in the generic investment transaction model, with AMFI master scheme/NAV history used to value legacy holdings.

It is built for **households, not just individuals**: a four-tier role hierarchy (System Owner / Super User / Admin / Viewer) plus many-to-many family membership lets several people share visibility into the same portfolio — or several portfolios — with permissions enforced independently on the backend, not merely hidden in the UI.

### Investment-driven market and NAV refresh

Automatic valuation refresh is investment-driven rather than a blanket provider sync. Active investment ISINs are collected from portfolio assets and legacy/imported mutual-fund transactions, then matched to the AMFI master **by ISIN**. The AMFI latest feed is downloaded once per refresh, but only records matching held investment ISINs are persisted. Mutual-fund history is backfilled from the persisted AMFI master history for MIS valuation.

A lightweight in-process scheduler runs the investment-driven AMFI refresh and MIS history prefetch every **30 minutes**. Daily market-price scheduling remains responsible for listed-stock/ETF market data. AMFI is used for mutual-fund NAVs; listed ETFs with an AMFI-looking ISIN are routed to market-price data instead. Manual prices remain authoritative over automatic sources.

### At a glance

|     | Highlight                     | What it means                                                                                 |
| --- | ----------------------------- | --------------------------------------------------------------------------------------------- |
| 📈  | **Real returns**              | XIRR, CAGR and realized / unrealized P&L computed from actual transactions                    |
| 🇮🇳  | **Built for India**           | Stocks, ETFs, bonds, SGBs, mutual funds and SIPs; Yahoo Finance + AMFI data                   |
| 👨‍👩‍👧  | **Family-ready**              | Four roles, many-to-many families, and an active-family switcher                              |
| 🤖  | **Deterministic intelligence** | Portfolio News classification and alerting run locally with rules; no AI API key is required |
| 📰  | **News that matters**         | An agent reads your _actual_ holdings, matches articles deterministically, then scores impact |
| 🪶  | **Low infrastructure overhead** | SQLite by default; optional Windows Task Scheduler for automatic hourly market-price refresh; no Celery or Redis required |
| 📤  | **Export anything**           | Transactions, holdings and summaries to Excel or PDF                                          |
| 📥  | **Controlled transaction imports** | Upload history, row-level failures and a downloadable standard transaction format              |
| 📊  | **MIS reporting**             | IPS, Data Sheet, Tax Report and Fund Type-wise Summary with persisted historical valuation and Excel download |
| 🔢  | **Flexible display units**    | View monetary values as Amount, Lakhs or Crores without changing stored rupee values          |
| 🔐  | **Backend-enforced security** | Every request re-derives role and family scope from the database                              |

### Design principles

1. **Numbers are computed, never guessed.** The AI layer only interprets figures the backend hands it.
2. **Transactions are the source of truth.** Holdings are derived and can be rebuilt at any time.
3. **The server decides who can do what.** The UI hides controls for convenience; the API blocks them for real.
4. **Role and family are separate ideas.** Role controls what you can _do_; family membership controls whose data you can _see_.
5. **Run it anywhere, simply.** One Django process, one Angular app, one database file to start.

---

## 🔗 Data sources

The application uses the following external data sources for market and investment data:

| Data | Source |
| --- | --- |
| Stocks and ETFs | [Yahoo Finance](https://finance.yahoo.com/) via `yfinance` |
| Mutual-fund latest NAVs | [AMFI India](https://www.amfiindia.com/) — AMFI latest NAV feed |
| Mutual-fund historical NAVs | [AMFI India](https://www.amfiindia.com/) — AMFI historical NAV report |
| Nifty 50 TRI | [NSE Indices](https://www.niftyindices.com/) — official Nifty 50 Total Return Index historical data |
| BSE 500 TRI | [BSE India](https://www.bseindia.com/) — BSE500T historical data; deployments may also use the configured BSE 500 TRI CSV/URL or the supported TRI-tracking ETF fallback |
| Portfolio news | [Google News](https://news.google.com/) RSS — no API key required |
| AI explanations | [Google Gemini](https://ai.google.dev/) — optional, only for Portfolio Chat; not required by Portfolio News |

| Corporate filings | NSE public Corporate Announcements feed; BSE via an authorized/configured feed |
| Portfolio News analysis | Deterministic local rules — no hosted AI model or paid AI API required |

### 📈 Watch List benchmark and indexed-performance charts

Watch List benchmark comparisons use **official Nifty 50 Gross TRI** data from NSE Indices and BSE 500 TRI data from the configured BSE source/fallback path.

The indexed-performance chart is deliberately protected against sparse source responses:

- Nifty 50 TRI history is accepted only when it has sufficient row density and covers the requested date range.
- If the Nifty endpoint returns sparse or incomplete data, PWMS automatically retries using smaller historical windows instead of rendering a sparse annual-point series.
- Mutual-fund benchmark charts use the shared **AMFI master NAV history** for the selected fund.
- Older Watch List entries that only contain the six performance-anchor snapshots (`1M`, `3M`, `6M`, `1Y`, `3Y`, `5Y`) are automatically backfilled with actual AMFI historical NAV observations when the chart is opened.
- The chart uses actual observations; PWMS does not interpolate synthetic NAV or benchmark values merely to make the line look denser.
- Historical coverage is validated using configurable minimum row density and date coverage thresholds before a source is considered complete.
- The selected chart period (`1M`, `3M`, `6M`, `1Y`, `3Y`, or `5Y`) determines the AMFI history that is prepared.

The thresholds and benchmark endpoints are centralized in [`backend/config/pwms_config.json`](./backend/config/pwms_config.json), so source URLs, timeouts and density requirements can be changed without editing the benchmark service code.
## 🚀 Quick start

> Full walkthrough with troubleshooting: **[SETUP.md](./SETUP.md)**. Prerequisites: Git, Python 3.12 (3.11+), Node.js 20+ (22 recommended).

**1 — Clone**

```bash
git clone https://github.com/avviiiral/Personal_Wealth_Monitoring_System.git
cd Personal_Wealth_Monitoring_System
```

**2 — Backend** (terminal 1)

<details open>
<summary><b>Windows (PowerShell)</b></summary>

```powershell
cd backend
python -m venv venv
.\venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

# Minimal local .env (Gemini is optional and only enables Portfolio Chat)
@"
DEBUG=True
GEMINI_API_KEY=paste-your-key-here  # optional: Portfolio Chat only
"@ | Set-Content .env

python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

</details>

<details>
<summary><b>macOS / Linux</b></summary>

```bash
cd backend
python3 -m venv venv
source venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

# Minimal local .env (Gemini is optional and only enables Portfolio Chat)
printf 'DEBUG=True\nGEMINI_API_KEY=paste-your-key-here  # optional: Portfolio Chat only\n' > .env

python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

</details>

**3 — Frontend** (terminal 2)

```bash
cd frontend
npm install
npm start
```

**4 — Open the app**

| Service             | URL                               |
| ------------------- | --------------------------------- |
| 🌐 Web app          | http://localhost:4200             |
| ⚙️ API health check | http://127.0.0.1:8000/api/health/ |

Log in with the account you just created, open **Portfolio → Import** and load your transactions workbook.

> 💡 **Local `.env` tip:** `backend/.env.example` is a _production-style_ template (HTTPS redirect, secure cookies). Do not copy it unchanged for local development — browsers reject `Secure` cookies over plain `http://localhost`, which breaks login. Use the minimal `.env` above, or see [SETUP.md](./SETUP.md#43-create-your-env-file).

<!--
📸 SCREENSHOTS — add your own images to docs/ and uncomment this block.

## 📸 Screenshots

| Dashboard | Portfolio tree |
|---|---|
| ![Dashboard](docs/screenshots/dashboard.png) | ![Portfolio](docs/screenshots/portfolio.png) |

| Analytics | AI chat | Portfolio news |
|---|---|---|
| ![Analytics](docs/screenshots/analytics.png) | ![AI chat](docs/screenshots/ai-chat.png) | ![News](docs/screenshots/news.png) |
-->

---

## ✨ Features

### 📊 Dashboard

Net worth, asset allocation, key portfolio metrics and an **Investment Summary** table broken down by asset class — all computed server-side and scoped to your own data plus your currently active family's data (or everyone's, for a System Owner).

- **Standard Allocation** supports both **Amount** and **Percentage** views.
- The **percentage allocation is authoritative**; the amount is recalculated from the current scoped total wealth.
- A global allocation can be shared across family members, while selecting a family member can create an independent family-specific allocation override.
- Family-specific amounts recalculate against that family member's current portfolio total without changing the configured allocation percentages.
- Standard Allocation percentages are validated to total **100%** before saving.

### 📁 Portfolio

- A hierarchical tree of holdings: **Family → Portfolio → Asset class → Sub-class → Asset**
- Quantity, invested value, current value, P&L and **XIRR per node**
- Full transaction history, create / edit / delete
- **Excel transaction import** (a _Transactions_ sheet is required; a _Summary_ sheet is optional)
- **Standard transaction format download** from Settings for consistent transaction uploads
- **Transaction upload history** with upload status, imported/failed row counts and row-level failure details
- **Data-driven classification** — Family Member, Asset Class, Sub Class, Asset Name, Underlying and Advisors are sourced from uploaded transaction/database records rather than hard-coded user-facing classification lists
- Inline **manual price override** for Admin and above, for any asset in your visible family scope
- **Asset Underlying uploads** from Portfolio for supported assets using the standard Excel format (`Stocks`, `% Holding`)
- **Underlying upload visibility in Settings** with asset name, uploader, upload timestamp, and expandable underlying holdings
- **Sample Underlying Format** download from Settings as an Excel workbook with an `Underlying` sheet and upload instructions
- **Transaction Edit History** records edits made from Transaction Reports and retains a durable audit record when a transaction is deleted. Deleted transactions remain visible in the audit history with their previous values.

### 📥 Transaction Uploads

Transaction imports are tracked as auditable upload records so users can review what happened after an Excel upload.

- Upload history is available from **Settings → Transaction Uploads**.
- Each upload records its processing status and row-level import statistics.
- Failed rows can be inspected through the upload details view.
- Users can download the **Standard Transaction Format** directly from Settings.
- The standard workbook includes the transaction fields expected by the importer and supports the existing `Summary` / portfolio-mapping structure.
- Transaction upload failures are recorded without silently treating invalid rows as successful imports.
- The importer accepts uploaded classification values as authoritative application data; it does not replace them with hard-coded asset-class or family-name values.

### 🧩 Asset Underlying management

Asset-level underlying holdings can be uploaded from the Portfolio page using an Excel workbook.

- The upload format uses **`Stocks`** and **`% Holding`** columns.
- Uploading an underlying replaces the current underlying snapshot for that asset/family.
- Each imported holding stores the uploader and upload timestamp for audit visibility.
- **Settings → Underlyings** shows the latest underlying upload for each asset in the active family, including uploader, upload time, and the underlying rows.
- The Settings page provides a downloadable **Sample Underlying Format** Excel workbook with example rows and a separate instructions sheet.
- Underlying holdings are stored as the current snapshot; the application does not currently retain every prior upload as a separate historical version.

### 🧩 Data-driven portfolio classification

Portfolio hierarchy and MIS grouping are driven by transaction/database data uploaded by the user.

The following user-facing fields are sourced from uploaded transaction records:

| Field | Source |
| --- | --- |
| **Family Member** | Uploaded transaction data |
| **Asset Class** | Uploaded transaction data |
| **Sub Class** | Uploaded transaction data |
| **Asset Name** | Uploaded transaction data |
| **Underlying** | Uploaded transaction data |
| **Advisors** | Uploaded transaction data |

The application no longer uses synthetic `Unassigned` values or hard-coded user-facing asset-class mappings for these fields. Internal technical asset categories may still be used by market-data services to decide which external price/NAV provider to call; those technical categories are separate from the user uploaded portfolio classification.
### 📊 Historical MIS valuation

The MIS historical valuation pipeline is designed to use the same portfolio transactions that drive the live portfolio, while valuing each asset with persisted historical market data.

- Historical valuation is calculated **server-side**; the Angular frontend only requests and displays the results.
- Equity transactions are kept separate from mutual-fund transactions during historical replay.
- Mutual funds are supported through both:
  - the dedicated `mutual_funds` transaction/NAV models; and
  - legacy/imported mutual-fund transactions stored in `investments.Transaction` and identified from their uploaded mutual-fund subclass.
- Legacy mutual-fund holdings are resolved from the asset/security **ISIN** to `AMFIMasterScheme`, then valued from `AMFIMasterNAV` history.
- AMFI master history is persisted locally, so an MIS request does not require a live AMFI download.
- Historical mutual-fund invested value, portfolio value and P&L are included in the returned wealth history rather than being misclassified as equity.
- The current-day historical point is reconciled with the canonical portfolio valuation so dashboard and historical wealth totals remain consistent.
- Historical valuation supports user-selected dates; available market/NAV history is used for the requested valuation date according to the backend's persisted-history rules.


### 📰 Portfolio News & Corporate Filing Intelligence

The **Portfolio News** feature is a deterministic, portfolio-aware news and corporate-filing monitoring layer. It does **not** require an AI model API key and does not add a hosted AI inference cost.

#### News monitoring

- Builds news searches from the securities actually held in the user's visible portfolio scope.
- Uses **Google News RSS** for general news discovery; no Google News API key is required.
- Matches articles to holdings using deterministic identifiers and text signals.
- Uses event clustering and source aggregation to avoid creating duplicate alerts when multiple sources report the same event.
- Classifies sentiment, impact, materiality, confidence and time horizon using transparent rule-based analysis.
- Calculates portfolio-aware alert priority from impact, holding weight, confidence, source quality and recency.
- Supports notification tiers:
  - **Critical / High** — eligible for immediate Web Push notification.
  - **Moderate** — stored for digest handling.
  - **Low / Very Low** — retained in the feed without an immediate notification.
- Includes an unusual-activity signal based on recent deterministic article-match volume versus the holding's baseline.
- The Portfolio News list supports live polling and incrementally refreshes recently changed alerts without requiring a full page reload.

#### Corporate filing intelligence

Corporate announcements are processed through a separate deterministic filing pipeline and can enrich an existing Portfolio News event instead of creating a duplicate alert.

Supported filing/event categories include, among others:

- Financial results and earnings-related announcements
- Board meetings and board-meeting outcomes
- Dividends, bonus issues and stock splits
- Buybacks, acquisitions, mergers and demergers
- Fundraising, preferential issues, warrants and capital allotments
- Promoter pledge / pledge release and shareholding changes
- CEO, CFO, director and auditor changes
- Auditor qualifications and regulatory actions
- Credit-rating changes and defaults
- Insolvency / bankruptcy and litigation
- Material contracts, orders and other material disclosures
- Investor presentations and earnings calls
- Shareholding patterns, related-party transactions and trading-window disclosures
- Regulation 30 / Regulation 32 disclosures and routine corporate updates

Filing severity is determined by deterministic event rules, including combined adverse signals where applicable. Filing sources contribute to event confidence, and the pipeline records the source and external filing identifiers for diagnostics.

#### Exchange feed behavior

- **NSE** uses the public NSE Corporate Announcements endpoint and establishes the required public session before requesting announcements.
- **BSE** is supported through an authorized/configured feed. The application skips an unconfigured BSE provider rather than failing the entire monitoring run.
- Filing ingestion is deduplicated using stable filing/source identifiers.
- Portfolio matching prioritizes **ISIN**, exchange symbol/BSE code and exact security/company identity. Generic company-name matching is deliberately conservative so similarly named securities are not incorrectly linked.
- Filing matching is limited to listed **stocks and ETFs**; mutual funds and non-listed portfolio assets are not treated as exchange-filing holdings.

#### Portfolio News detail experience

The detail page exposes:

- Why the event matters to the portfolio
- Deterministic source-reported summary
- Potential portfolio implication
- Sentiment, impact, materiality and impact score
- Relevance score, portfolio weight and confidence
- Time horizon and internal alert priority
- Source links and source tiers
- Deterministic interpretation and uncertainty notes

The frontend uses the shared light/dark design tokens, renders asynchronous API responses explicitly, and keeps the detail metrics responsive across desktop, tablet and mobile layouts.

#### Portfolio Impact & Alert Scoring

Portfolio News uses a **deterministic internal scoring model**. The numerical scores are not supplied by Google News, NSE, BSE, SEBI, or an external financial-risk API, and Portfolio News does not call an LLM to generate these scores.

The external sources provide the underlying facts (article/filing text, publisher, publication time, company identifiers), while PWMS converts those facts and the user's live portfolio exposure into transparent numerical factors.

| Score / Factor | Source in PWMS | How it is calculated |
| --- | --- | --- |
| **Event Impact Score** | Local RuleBasedArticleAnalyzer; corporate filings use the deterministic filing_intelligence classifier | News impact starts from **20** and increases from detected event signals. Critical signals add up to **38 points each** (maximum two), high-impact signals add up to **22 points each** (maximum three), and additional medium signals add up to **8 points each** (maximum four). Tier 1 sources add 8 points and Tier 2 sources add 4 points. The final news impact score is capped at **100**. Corporate filings use their deterministic event rules and representative severity scores: **INFO 20, LOW 30, MEDIUM 50, HIGH 70, CRITICAL 90**. |
| **Portfolio Weight** | Live PWMS holdings from the unified wealth analytics layer | Holding Current Value ÷ Total Current Portfolio Value × 100. The weight is calculated from the current equity + mutual-fund portfolio values for the monitored user. It is captured as portfolio_weight_at_alert when the alert is created. |
| **Confidence** | Local RuleBasedArticleAnalyzer | Starts at **0.78**. It is reduced by **0.12** when the RSS article has no description, by **0.08** when the publisher is missing or reported only as Google News, and by another **0.08** for broad sector/macro queries that have no direct holding-name hit. The final confidence is bounded to **0.35–0.95**. |
| **Source Quality** | Local publisher/source classification in source_quality.py | Publisher names are matched against deterministic Tier 1/Tier 2 lists, with optional configured overrides. **Tier 1 = 1.00**, **Tier 2 = 0.75**, **Tier 3 = 0.50**. Unknown/empty publishers default to Tier 3. For clustered sources, the best available tier is retained. |
| **Recency** | Article/filing publication timestamp | **≤1 day = 1.00**. Between **1 and 7 days**, the multiplier decreases linearly from **1.00 to 0.50**. **≥7 days = 0.50**. If publication time is unavailable, the neutral value **1.00** is used rather than penalizing missing metadata. |
| **Final Alert Score** | Local compute_alert_score() formula | Impact Score × (Portfolio Weight % ÷ 100) × Confidence × Source Quality × Recency. The result is rounded to two decimals and constrained to **0–100**. |

##### Event Impact: ordinary news

The local news analyzer looks at the article title and provider-supplied description/snippet. It uses transparent keyword/event rules rather than an LLM.

The main impact signal groups are:

- **Critical:** fraud/accounting fraud, insolvency/bankruptcy, default, licence cancellation/revocation, major regulatory action, regulatory/trading ban.
- **High:** major penalty, material litigation, regulatory action, merger/acquisition/takeover, CEO/CFO resignation, guidance cuts/withdrawal, plant/business shutdown, major/material orders or contracts, promoter pledge/increase, major investigation.
- **Medium:** earnings/results, guidance, orders, dividends, buybacks, approvals, fundraising, rating upgrade/downgrade, lawsuits/investigations, profit/revenue decline, margin decline and similar events.

The analyzer also determines **sentiment, category, materiality and time horizon** from the same deterministic rule set. These are analytical attributes; they are not additional multipliers in the final alert-score formula.

Impact levels are mapped deterministically from the 0–100 impact score:

| Impact score | Impact level |
| ---: | --- |
| **0–20** | Very Low |
| **21–40** | Low |
| **41–60** | Moderate |
| **61–80** | High |
| **81–100** | Critical |

##### Corporate filing impact

Corporate filings have a dedicated deterministic classifier because exchange announcements contain structured event information. Representative filing scores are:

| Filing severity | Representative impact score |
| --- | ---: |
| **INFO** | 20 |
| **LOW** | 30 |
| **MEDIUM** | 50 |
| **HIGH** | 70 |
| **CRITICAL** | 90 |

Individual filing event rules have their own base scores and can combine contextual/adverse signals before severity is assigned. The filing then enters the shared Portfolio News alert-scoring path.

##### Portfolio Weight calculation

For every monitored holding:

Portfolio Weight % = Holding Current Value ÷ Total Current Portfolio Value × 100

Example:

₹2,00,000 ÷ ₹10,00,000 × 100 = **20%**

The final formula uses **0.20**, not 20, for the multiplication.

This is why the same company event can have different portfolio impact for different users:

1% exposure → lower portfolio-weight contribution

30% exposure → higher portfolio-weight contribution

##### Confidence calculation

Confidence is a **deterministic data-quality / matching-confidence factor**, not a probability of future returns.

Current rule:

- Base confidence: **0.78**
- No article description: **−0.12**
- Missing publisher / publisher reported only as Google News: **−0.08**
- Broad sector/macro query with no direct holding-name hit: **−0.08**
- Final value is bounded between **0.35 and 0.95**

For example:

0.78 − 0.12 − 0.08 = **0.58**

if both the description and usable publisher are missing.

##### Source-quality calculation

Source quality is assigned from the publisher name using deterministic substring matching.

**Tier 1** includes primary/official and top financial sources such as exchange/regulator sources and publishers including Reuters, Bloomberg, CNBC, Financial Times, Economic Times, Moneycontrol, Business Standard and Mint/Livemint.

**Tier 2** includes reputable business/general financial publishers such as The Hindu, BusinessLine, Business Today, Financial Express, NDTV Profit/Business, Zee Business, CNBC-TV18, Forbes India and similar configured sources.

**Tier 3** is the fallback for all other, unknown or unclassified publishers.

The multiplier is:

Tier 1 = **1.00**

Tier 2 = **0.75**

Tier 3 = **0.50**

For multiple reports clustered around the same event, source aggregation retains supporting source records while using the best available source tier for scoring.

##### Recency calculation

Recency is a deterministic linear decay:

Age ≤ 1 day → **1.00**

1 < Age < 7 days → **1.00 − ((Age − 1) ÷ 6 × 0.50)**

Age ≥ 7 days → **0.50**

A missing publication date receives **1.00** so missing metadata does not automatically make a story look stale.

##### Final mathematical calculation

The complete current formula is:

Final Alert Score = Event Impact × (Portfolio Weight % ÷ 100) × Confidence × Source Quality × Recency

Example:

Event Impact = **90**

Portfolio Weight = **20%**

Confidence = **0.90**

Source Quality = **1.00**

Recency = **1.00**

Therefore:

90 × 0.20 × 0.90 × 1.00 × 1.00 = **16.20**

So the stored **alert score is 16.20**.

The score is an **internal alert-priority/ranking number**. It is **not** a prediction of future returns, a probability of loss, a VaR calculation, a credit score, or investment advice.

##### Score versus notification tier

The **final alert score is a ranking/relevance signal** and is separate from the notification tier.

Notification tier is derived from the analyzed **impact level**:

| Impact level | Notification behavior |
| --- | --- |
| **Critical** | Immediate notification eligible |
| **High** | Immediate notification eligible |
| **Moderate** | Daily digest |
| **Low / Very Low** | Feed/history only |

The configured minimum alert score (default **2.0**) controls whether an alert remains user-facing/relevant, but a high/critical impact event can still be treated as an immediate-notification candidate based on its notification tier. The score itself does not predict investment performance.

##### Where the numbers come from

The data flow is:

News / Filing Sources → Facts → Deterministic Classification → Impact + Confidence + Source Quality + Recency → Portfolio Weight → Final Alert Score → Notification Handling

External providers supply the **facts**. PWMS supplies the **rules and mathematics**. The current numeric weights are **internal design parameters**, not values prescribed by SEBI, NSE, BSE, or a universal financial-risk standard. They can therefore be recalibrated later using historical outcomes without changing the data-ingestion architecture.

#### Zero-cost design

Portfolio News and corporate filing intelligence are intentionally implemented without an LLM dependency:

`NewsArticle`, source records, event clustering, deterministic classification, filing intelligence, portfolio matching, scoring and notification decisions all execute in the application/backend.

Optional **Gemini** remains separate for Portfolio Chat and is not required for Portfolio News.

### 📈 Analytics

- Wealth allocation by **asset class, sector, market cap, AMC and advisor**
- Performance ranking and portfolio **XIRR**
- **Historical wealth** over any date range
- Dedicated **equity** and **fixed-income** breakdowns

### 🏦 Mutual funds and SIPs

- Scheme holdings with **NAV-based valuation** (AMFI feed)
- Mutual-fund transaction recording
- **SIP creation**, due / overdue tracking and **per-installment execution**
- SIPs are synced and executed automatically by the daily job

### 📄 Reports

Export **transactions, holdings and portfolio summaries** to **Excel or PDF** — matching exactly what the Portfolio and Dashboard pages show.

### 📑 MIS Report

The **MIS Report** is available at **Portfolio → MIS Report** and contains the following sections:

- **IPS** — current and prior-period market values by asset class and family, including differences and grand totals.
- **Data Sheet** — investment cost, opening MTM, period transactions, and closing MTM with grouped section headers from the MIS format.
- **Tax Report** — the Data Sheet structure plus four taxation columns: **Realized P/L**, **Unrealized P/L**, **Realized Tax**, and **Unrealized Tax**.
- **Fund Type-wise Summary** — `Fund Type.V2` maps to **Asset class**, `Fund Name` maps to **Asset name**, and `Total` is the **Current Market Value**.
- **Notes** — standard MIS reference-rate observations for REITs/InvITs, Sovereign Gold Bonds, Silver ETF, Nifty 50, BSE 500 and Dollar Rate, including opening/closing values, change and percentage change where source data is available.
- **Custom date range** — select `From Date` and `To Date`; opening valuation is reconstructed from the day before the selected start date and closing valuation is calculated as of the selected end date.
- **Display As** — **Amount**, **Lakhs**, or **Crores**. Conversion is presentation-only; source financial values remain in rupees.
- **FIFO taxation** — realized P/L is calculated using **First-In, First-Out (FIFO)** transaction matching. Tax is calculated separately for realized and unrealized P/L using the configured asset-specific tenure and tax rates. The same tenure-based rate is applied regardless of P/L sign, so a negative realized or unrealized P/L produces a **negative tax value (tax benefit)** in the report.
- **Tax Settings** — taxation settings are configured by **Asset Name** for the active family, including **Tenure (Months)**, **Short Term Tax**, and **Long Term Tax**. The settings apply consistently to matching asset names within the family.
- **Download Excel** — exports `IPS`, `Data Sheet`, `Tax Report`, `Fund Type Summary`, and `Notes` in `.xlsx` format.
- **Uploaded classification source** — IPS and related MIS grouping use the stored/uploaded family and asset classification data rather than synthetic classification defaults.
- **Asset-name basis** — MIS grouping uses the stored/uploaded **Asset Name** and **Family Member** values where those fields are part of the report grouping.
- **Standard transaction workbook** — the separate transaction template download is available from Settings and is intended for future transaction imports.
- **Indian INR formatting** — monetary values in the downloaded **Data Sheet** and **Tax Report** use the **₹ symbol with Indian lakh/crore comma grouping** (for example, `₹1,20,880.00`). Quantity/unit columns remain numeric without the currency symbol.
- **Automatic reference-price refresh** — MIS reference prices are refreshed automatically by the background scheduler every 30 minutes, are included in the scheduled refresh flow, and are refreshed again immediately before an MIS Excel download. The refresh uses stored market history and supported Yahoo Finance symbols; unavailable third-party data is not fabricated.

### 🗂️ MIS historical valuation data

MIS historical valuations use **stored market history**, not live provider calls while the interactive report is being generated.

- **Mutual-fund historical NAVs** are stored in the shared **AMFI master NAV history** (AMFIMasterNAV) and are the source used by MIS for historical MF valuations.
- The MIS history prefetcher keeps held schemes covered for approximately **five years** and imports only missing date ranges.
- Historical AMFI coverage is prepared automatically **after transaction imports** and during the **daily scheduled refresh**.
- The interactive MIS endpoint is intentionally **database-only** for historical MF valuation. It does not wait for an AMFI download while the user is viewing the report.
- The explicit **Excel download** path can perform a targeted AMFI history backfill when required before generating the workbook.
- Existing historical price/NAV rows are reused across MIS valuations instead of downloading the same history repeatedly.
- The manual historical import command supports a complete date range when an initial database backfill is required:

```bash
python manage.py fetch_amfi_nav --from-date 2025-10-01 --to-date 2026-10-05
```

This architecture keeps the report responsive while ensuring historical Data Sheet, IPS and Tax Report valuations can be reconstructed from persisted observations.

### 🔐 Settings

- **Account and preferences**, including password change
- **Tax Settings** — configure asset-name-specific tenure, Short Term Tax and Long Term Tax rates for the active family.
- **User Management** — role-scoped: you only see and manage the roles you're allowed to
- **Family Management** — System Owner only
- **Logs** — operational and audit history is grouped under one Settings entry. Logs contains:
  - **Tax Update** — history of tax-setting changes, including user, timestamp, asset name, previous values and new values.
  - **Transaction Edit History** — audit history of transaction edits and deletions. A deleted transaction is retained as an audit record and displayed as **Transaction Deleted**, with the original transaction values available from **View**.
  - **Transaction Uploads** — upload status, imported/failed/duplicate counts, row-level failures and standard transaction template download.
  - **Underlyings** — latest underlying upload per asset, uploader, upload time, underlying rows and sample Excel format.
  - **MIS Edit History** — audit history of editable MIS Notes changes.
  - **Manual Prices** — override any asset's price within your visible family scope; every override is **audit-logged** (who, when, from what).

### 📰 Portfolio News workflow


The Portfolio News pipeline is fully deterministic: retrieval, portfolio matching, event classification, alert scoring and notification tiering run locally without a hosted AI model. The **News** layer is metadata-first; the alert layer is classified locally without an AI API.

```text
┌──────────────────────────────┐
│       ACTIVE FAMILY          │
│                              │
│ Family Stocks / Mutual Funds │
└──────────────┬───────────────┘
               │
               ▼
┌──────────────────────────────┐
│   HOLDINGS REGISTRY          │
│                              │
│ Builds live holdings         │
│ + names / aliases / symbols  │
│ + ISIN / scheme information  │
└──────────────┬───────────────┘
               │
               ▼
┌──────────────────────────────┐
│       QUERY BUILDER          │
│                              │
│ Creates search queries       │
│ for each portfolio holding   │
└──────────────┬───────────────┘
               │
               ▼
┌──────────────────────────────┐
│     GOOGLE NEWS RSS          │
│                              │
│ Fetches matching news        │
│ articles                     │
└──────────────┬───────────────┘
               │
               ▼
┌──────────────────────────────┐
│      HOLDING MATCHER         │
│                              │
│ Deterministically checks     │
│ headline + body/description  │
│ ↔ portfolio holding          │
└──────────────┬───────────────┘
               │
               ▼
        ┌─────────────────┐
        │ PortfolioNews   │
        │     Match       │
        └────────┬────────┘
                 │
                 │
       ┌─────────┴──────────┐
       │                    │
       ▼                    ▼
┌───────────────┐   ┌──────────────────┐
│   ALL NEWS    │   │      GEMINI       │
│               │   │   AI ANALYSIS     │
│ No Gemini     │   │                  │
│ required      │   │ Relevance        │
│               │   │ Sentiment        │
│ Raw matched   │   │ Impact           │
│ articles      │   │ Category         │
└───────┬───────┘   └────────┬─────────┘
        │                    │
        ▼                    ▼
┌───────────────┐   ┌──────────────────┐
│ /news/raw/    │   │ PortfolioNews    │
│               │   │ Alert            │
└───────┬───────┘   └────────┬─────────┘
        │                    │
        │                    ▼
        │             ┌──────────────┐
        │             │   AI FEED    │
        │             └──────┬───────┘
        │                    │
        │                    ▼
        │             ┌──────────────┐
        │             │    DIGEST    │
        │             └──────────────┘
        │
        ▼
┌────────────────────────────────────┐
│          PORTFOLIO NEWS UI         │
│                                    │
│  [ News ] [ Alerts ] [Digest] │
└────────────────────────────────────┘
```

**Workflow summary:**

1. The **Active Family** supplies the current stocks and mutual funds across the family-scoped portfolio.
2. The **Holdings Registry** builds the live family holding set and its searchable identifiers, including names, aliases, symbols, ISINs and scheme information where available.
3. The **Query Builder** creates search queries for each portfolio holding.
4. **Google News RSS** retrieves matching articles.
5. The **Holding Matcher** deterministically checks both the news headline and the provider-supplied article body/description against the holding's names, aliases, ticker and ISIN. For underlying queries it applies the same headline/body check to the requested underlying name or ISIN.
6. Each deterministic association is persisted as a **PortfolioNewsMatch** record.
7. **News** reads these raw family matches through `/api/ai/news/raw/` and does not require paid AI.
8. The local **RuleBasedArticleAnalyzer** enriches matched articles with relevance, sentiment, impact and category information and stores the resulting **PortfolioNewsAlert** records.
9. The enriched records power the **Alerts** and **Digest** views.
10. The **Portfolio News UI** presents the three layers as **News**, **Alerts**, and **Today's Digest**.

This separation means a news article can appear in **News** independently of analysis; the alert layer is now generated by the local rule-based analyzer.


### Corporate Filing Intelligence

Corporate filings are first-class Portfolio News inputs, distinct from ordinary news. The existing `filing_intelligence` app stores filing metadata, performs deterministic classification, matches securities by ISIN/symbol/company identity, and feeds the existing PortfolioNewsAlert/Web Push/digest path.

**Supported event families include:** financial results, board meetings, dividends, bonus issues, stock splits, buybacks, acquisitions, mergers, demergers, fund raising, preferential issues, promoter pledges/releases, management/auditor changes, regulatory action, credit-rating downgrades, defaults, litigation, material contracts/orders, shareholding/promoter transactions, investor presentations, earnings calls and routine announcements.

**Event clustering:** NSE/BSE/company filings and independent news reports are stored through the same `NewsArticle`/`NewsArticleSource` layer. URL, normalized-title, recent fuzzy-title and conservative event-family/entity matching collapse reports of the same event into one underlying article while retaining supporting source URLs and source count. A later primary filing can upgrade an existing clustered alert rather than creating another notification.

**Source priority:** exchange/company/regulatory evidence is treated as Tier 1. Cross-source confidence increases only when distinct URLs/publishers are attached; syndicated copies are not intentionally counted as independent confirmation.

**Official-source limitation:** NSE publicly exposes Corporate Filings/Announcements pages with CSV download controls, while BSE exposes Corporate Data through its official market-data products. This branch does not bypass anti-bot controls or claim an undocumented production endpoint is available. The production adapters therefore accept an authorized HTTP/CSV/JSON feed via `EXCHANGE_FILING_FEED_URL_NSE` / `EXCHANGE_FILING_FEED_URL_BSE`. If a feed is unavailable, that exchange fails independently and no fake filing is created.

**Commands:**

```powershell
python manage.py ingest_exchange_filings --exchange=nse --hours=24 --dry-run
python manage.py ingest_exchange_filings --exchange=bse --hours=24 --dry-run
python manage.py ingest_exchange_filings --hours=24
```

The background scheduler runs filing ingestion only when corporate filing intelligence is enabled and an exchange feed is configured. This keeps the default deployment zero-cost and quiet when no authorized feed endpoint is available.

### Family-scoped Portfolio News

Portfolio News follows the same family boundary used by the portfolio and analytics views.

- The **active family** is the authoritative scope for **News** for users who belong to a family.
- Deterministic matches are stored with a **FamilyGroup** reference in `PortfolioNewsMatch`.
- The same article/holding/connection combination is stored only once per family, while separate underlying relationships can be retained when one article is connected through more than one uploaded underlying.
- The raw endpoint `/api/ai/news/raw/` returns only the currently selected family's deterministic news for normal users.
- The response includes `family_id` and `family_name` so the UI can make the active family scope explicit.
- A user with no family retains access to legacy user-scoped raw matches; this is a compatibility path and does not weaken family authorization.
- System Owners can view stored family-scoped raw news across families.
- Family membership controls **visibility**; role-based permissions continue to control what users can do. Client-supplied family IDs are not trusted for authorization.
- **News remains analysis-independent.** The local rule-based analyzer produces the `PortfolioNewsAlert` records used by Alerts and Today's Digest.
- **Asset-underlying relationships are explicit.** Uploaded `AssetUnderlyingHolding` rows are loaded into the news holdings registry. The monitor issues bounded queries for the largest uploaded underlyings and only accepts an underlying match when that underlying's name or ISIN appears in the news headline or provider-supplied body/description.
- **Deterministic matching uses headline + body/description.** Portfolio News does not rely on headline-only matching. A holding can match when its identifier appears in either the headline or the provider-supplied article description/body snippet. Google News RSS does not provide the publisher's complete article body, so this layer does not perform an additional full-article fetch just for deterministic matching.
- **News connection metadata is persisted.** Each raw/AI relationship records `connection_type` (`direct` or `underlying`), the matched `underlying_name`, and the uploaded `underlying_weight`. The UI displays this connection so users can see why an article is associated with an asset.
- **Underlying news is deterministic first.** The local analyzer receives the deterministic connection as context and does not invent the asset-to-underlying relationship.



### Portfolio News data flow details

The raw and AI paths intentionally share the same deterministic retrieval stage:

```text
Portfolio holdings
      ↓
Holdings Registry
      ↓
Query Builder
      ↓
Google News RSS
      ↓
Holding Matcher
(headline + body/description)
      ↓
PortfolioNewsMatch
      ├──→ /api/ai/news/raw/ → News
      │
      └──→ local rule-based analysis
                ↓
          PortfolioNewsAlert
                ├──→ Alerts
                └──→ Today's Digest
```

- **`PortfolioNewsMatch` is the raw-news persistence layer.** A match stores the article, holding type/id, holding display name, matched query and, for family-scoped matches, the authoritative `FamilyGroup`. The database prevents the same article/holding combination from being stored more than once within the same family. Users without a family retain the legacy user-scoped uniqueness path.
- **No paid AI call is part of article retrieval or analysis.** Google News RSS retrieves articles, the deterministic matcher associates them with portfolio holdings, and the local analyzer classifies them.
- **Analysis limits do not remove raw matches.** Articles matched by the deterministic layer remain available to **News** even if local analysis is disabled or fails.
- **News is metadata-first.** The raw endpoint returns the article title, original URL, source, description, publication time, source quality/count, matched query and matched holdings; users can open the original publisher article from the UI.
- **Family visibility is enforced server-side.** For normal users, the raw endpoint derives the active family from the authenticated user and returns only that family's deterministic matches. System Owners can view stored family-scoped matches across families. Users without a family use the legacy user-scoped path. Client-supplied family IDs are not trusted for authorization.
- **Monitoring is automatic.** The `monitor_portfolio_news` management command performs the news-monitoring pass and is also used by the application's background scheduler.
- **Gemini is not required for Portfolio News.** A Gemini key is only used by the separate Portfolio Chat feature.

### 🤖 AI Portfolio Chat

A **Gemini-backed** assistant scoped to the logged-in user's own portfolio. The backend builds a structured context (holdings, allocation, recent performance) and hands it to Gemini. **Gemini interprets the numbers it is given — it never computes or invents them.** Token usage is logged per call.

### 📰 Portfolio News Intelligence

Portfolio News has deterministic news, corporate filings, alerts and digest layers. **News** shows every article that the deterministic portfolio matcher fetched and associated with the user's holdings. **Alerts** and the digest use the same stored alerts, now enriched by the zero-cost rule-based analyzer. The raw layer stores only article metadata/snippets and links users to the original publisher.

A background agent that:

1. reads each user's **actual holdings** (there is no hard-coded stock list),
2. searches **Google News RSS** (no paid news API),
3. **deterministically matches** articles to holdings,
4. classifies sentiment/category/materiality/impact using local keyword and event rules,
5. scores each alert by the existing **`impact × portfolio_weight × confidence`** formula plus source-quality and recency multipliers,
6. raises a **browser notification** for Critical / High items.

It is fully automatic — no paid AI key is required. Browser push delivery still requires the existing VAPID configuration. Critical / High alerts can be delivered through the browser Push API even when the Angular page is closed.

---

## 🧭 How it works

### 1 · System architecture

```mermaid
flowchart LR
    subgraph Client["Browser"]
        UI["Angular 21 SPA<br/>standalone components<br/>Chart.js · jsPDF · ExcelJS"]
    end

    subgraph Server["Django 5.2 + DRF"]
        API["REST API<br/>session auth + CSRF"]
        RBAC["users.permissions<br/>role + family scope"]
        APPS["Domain apps<br/>portfolio · analytics · mutual_funds<br/>investments · ai · portfolio_news"]
        SCH["In-process background jobs"]
    end

    WTS["Windows Task Scheduler<br/>Hourly market-price refresh"]

    DB[("SQLite - WAL mode<br/>or PostgreSQL")]
    YF["Yahoo Finance<br/>stocks and ETFs"]
    AMFI["AMFI<br/>mutual fund NAVs"]
    GN["Google News RSS"]
    GEM["Google Gemini API<br/>optional: AI Chat"]
    NLG["RuleBasedArticleAnalyzer<br/>local NLP"]

    UI -->|"JSON over HTTP"| API
    API --> RBAC --> APPS --> DB
    SCH --> DB
    SCH --> YF
    SCH --> AMFI
    SCH --> GN
    SCH --> NLG
    WTS -->|"update_market_prices"| APPS
    APPS -->|"AI chat"| GEM
```

### 2 · From transactions to insight

```mermaid
flowchart TD
    A["Excel import<br/>or manual entry"] --> U["Upload audit<br/>status + failures"]
    U --> B["Transactions<br/>source of truth"]
    B --> C["Holdings<br/>derived and rebuildable"]
    C --> D["Post-import price refresh"]
    D --> E["MarketPrice<br/>Yahoo · AMFI · Manual"]
    E --> F["Server-side calculations<br/>P&L · XIRR · CAGR · allocation"]
    C --> F
    F --> G["Dashboard · Portfolio · Analytics"]
    F --> H["Excel and PDF reports"]
    F --> I["Structured context for AI chat"]
    I --> J["Gemini explains<br/>never calculates"]
```

### 3 · Request lifecycle and authorization

Nothing trusts a role or family ID claimed by the client — every check is re-derived from the database on every request.

```mermaid
sequenceDiagram
    autonumber
    participant B as Browser - Angular
    participant D as Django REST API
    participant P as users.permissions
    participant DB as Database

    B->>D: Request + session cookie (CSRF token on writes)
    D->>D: Authenticate the session
    D->>P: Who is this user and what may they do?
    P->>DB: Re-derive role and family memberships
    DB-->>P: UserProfile + FamilyMembership
    P-->>D: Role check + visible owner IDs
    alt Not permitted
        D-->>B: 403 Forbidden
    else Permitted
        D->>DB: Query only rows owned by visible owners
        DB-->>D: Rows
        D-->>B: JSON response
    end
```

### 4 · Portfolio News agent pipeline

```mermaid
flowchart TD
    T["Timer<br/>every NEWS_MONITOR_INTERVAL"] --> H["Read each user's active holdings"]
    H --> Q["Build search queries<br/>from real holdings"]
    Q --> R["Google News RSS<br/>via feedparser"]
    R --> D["De-duplicate<br/>NewsArticle + NewsArticleSource"]
    D --> M{"Deterministic match<br/>to a holding?"}
    M -- no --> X["Discard<br/>no AI call spent"]
    M -- yes --> G["Local rule-based analysis<br/>impact + confidence"]
    G --> S["Score = impact × portfolio weight × confidence"]
    S --> A["PortfolioNewsAlert<br/>unique per user, article, holding"]
    A --> C{"Critical or High?"}
    C -- yes --> N["Web Push<br/>service worker + VAPID"]
    C -- no --> P["Listed on the Portfolio News page"]
```

### 5 · Scheduler start-up

```mermaid
flowchart LR
    R["Django starts<br/>runserver · waitress · uvicorn"] --> G{"scheduler_guard<br/>correct process?"}
    G -- yes --> J1["Price refresh<br/>every 15 min"]
    G -- yes --> J2["Daily refresh<br/>once per day of uptime"]
    G -- yes --> J3["Post-import refresh<br/>on demand"]
    G -- yes --> J4["News monitor<br/>every 30 min"]
    G -- no --> Z["Skip<br/>avoids duplicate schedulers"]
```

---

## 🔑 Roles and permissions

Four hierarchical roles are enforced on **every request** by one centralized permission service ([`backend/users/permissions.py`](./backend/users/permissions.py)). The frontend hides controls for convenience; the backend independently blocks anything unauthorized, no matter what the UI shows.

```
VIEWER  <  ADMIN  <  SUPER_USER  <  SYSTEM_OWNER
```

```mermaid
flowchart LR
    O["System Owner<br/>everything + families"] -->|"creates and manages"| S["Super User"]
    S -->|"creates and manages"| A["Admin"]
    A -->|"creates and manages"| V["Viewer"]
```

> **Role ≠ family.** Role decides what a user can **do**. Family membership decides whose data they can **see**. The two are deliberately never inferred from one another.

### Permission matrix

| Capability                                                                     | 👁️ Viewer | 🛠️ Admin | ⭐ Super User | 👑 System Owner |
| ------------------------------------------------------------------------------ | :-------: | :------: | :-----------: | :-------------: |
| Log in                                                                         |    ✅     |    ✅    |      ✅       |       ✅        |
| View permitted portfolio data (Dashboard, Portfolio, Analytics, AI Chat, News) |    ✅     |    ✅    |      ✅       |       ✅        |
| Edit own profile                                                               |    ✅     |    ✅    |      ✅       |       ✅        |
| Edit manual prices (family-shared)                                             |    ❌     |    ✅    |      ✅       |       ✅        |
| Create a Viewer                                                                |    ❌     |    ✅    |      ✅       |       ✅        |
| Create an Admin                                                                |    ❌     |    ❌    |      ✅       |       ✅        |
| Create a Super User or System Owner                                            |    ❌     |    ❌    |      ❌       |       ✅        |
| Manage Viewers ¹                                                               |    ❌     |    ✅    |      ✅       |       ✅        |
| Manage Admins ¹                                                                |    ❌     |    ❌    |      ✅       |       ✅        |
| Manage Super Users / System Owners ¹                                           |    ❌     |    ❌    |      ❌       |       ✅        |
| Change another user's role ²                                                   |    ❌     |    ❌    |    Limited    |       ✅        |
| Create / manage families and memberships                                       |    ❌     |    ❌    |      ❌       |       ✅        |
| Assign a user to multiple families                                             |    ❌     |    ❌    |      ❌       |       ✅        |
| View every family's data                                                       |    ❌     |    ❌    |      ❌       |       ✅        |

¹ _Manage_ = edit, activate, deactivate, delete and reset password. Account management is scoped **by role only** — family membership never gates it.
² A Super User may only move accounts between **Admin** and **Viewer**, and only when the target is currently Admin or Viewer. Nobody — including the System Owner — can change their **own** role (privilege-escalation guard), and the last active System Owner can never be demoted.

### What each user can see

| Role               | Visible portfolio data                                                    |
| ------------------ | ------------------------------------------------------------------------- |
| **System Owner**   | Every user in the system, across all families                             |
| **Everyone else**  | Their own data **plus** every member of their **currently active family** |
| No family assigned | Only their own data                                                       |

A user may belong to **zero, one or many** families. A personal **active-family selector** scopes every data screen to one family at a time — a combined view of several families is deliberately not shown.

> [!NOTE]
> `Asset` and `Transaction` records are still stored against a single owning user account. Family-shared _visibility_ is layered on top through the authorization helpers in `users/permissions.py`, not by changing the ownership field.

---

## 🧰 Tech stack

| Layer                 | Technology                                                                                                                               |
| --------------------- | ---------------------------------------------------------------------------------------------------------------------------------------- |
| Backend framework     | Django 5.2 + Django REST Framework 3.18                                                                                                  |
| Backend language      | Python 3.12                                                                                                                              |
| Database              | SQLite by default (WAL journal mode + busy-timeout for scheduler concurrency); **PostgreSQL** supported via `DATABASE_ENGINE=postgresql` |
| Production serving    | `runserver` for development; **waitress** (WSGI) or **uvicorn** (ASGI) for production                                                    |
| Authentication        | Django session authentication (cookie + CSRF)                                                                                            |
| Frontend framework    | Angular 21 — standalone components, no NgModules                                                                                         |
| Frontend language     | TypeScript                                                                                                                               |
| Charts                | Chart.js / ng2-charts                                                                                                                    |
| Excel import / export | `openpyxl` (backend), `exceljs` (frontend)                                                                                               |
| PDF export            | `jspdf` + `jspdf-autotable` (frontend)                                                                                                   |
| Market data           | Yahoo Finance (`yfinance` + `curl_cffi`), AMFI NAV feed over HTTP                                                                        |
| News retrieval        | Google News RSS via `feedparser` — no paid news API                                                                                      |
| AI                    | Google Gemini REST API — optional for Portfolio Chat; Portfolio News uses local rule-based NLP only                                                                 |
| Background scheduling | In-process Python threads — **no Celery, no Redis**                                                                                      |
| Logging               | Centralized rotating file handler (`backend/logs/pwms.log`)                                                                              |

---

## 📂 Repository structure

```
Personal_Wealth_Monitoring_System/
├── backend/
│   ├── manage.py
│   ├── requirements.txt
│   ├── .env.example            Template for every deployment-sensitive setting
│   ├── config/                 Settings (env-driven), scheduler_guard.py, root urls.py, WSGI/ASGI
│   ├── api/                    Health check, login / logout, profile settings
│   ├── users/                  RBAC: 4-tier roles, FamilyGroup, FamilyMembership,
│   │                           UserAuditLog, centralized permissions.py
│   ├── investments/            Asset, Transaction, Holding, SecurityMaster,
│   │                           Excel importer, AMC-name / quant enrichment
│   ├── market_data/            MarketPrice, price providers, background scheduler
│   ├── portfolio/              Portfolio tree / summary / holdings / transactions APIs
│   ├── mutual_funds/           Schemes, NAVs, MF transactions, SIPs, MF holdings
│   ├── analytics/              Wealth / allocation / performance / XIRR analytics
│   ├── ai/                     Gemini portfolio chat, Gemini usage tracking
│   ├── portfolio_news/         News discovery, matching, de-duplication, alert scoring
│   └── data/
│       ├── security_master.xlsx            Reference security data
│       └── security_master_lookups.json    Researched sector / AMC / ratio data
│
├── frontend/
│   ├── package.json · angular.json
│   └── src/
│       ├── environments/       environment.ts (dev) and environment.prod.ts
│       │                       (swapped at build time) — the ONE place the API URL is set
│       ├── app/                Shell, layout (sidebar / header with family switcher), routing
│       ├── core/
│       │   ├── services/       One API client per backend area + RBAC service
│       │   │                       includes MIS Report API / Excel download service
│       │   └── guards/         auth.guard (also loads the RBAC role)
│       ├── features/
│       │   ├── dashboard/  portfolio/  mis-report/  analytics/  reports/
│       │   ├── ai-chat/  portfolio-news/  login/
│       │   └── settings/
│       │       ├── user-management/
│       │       ├── family-management/
│       │       └── manual-prices/
│       └── shared/
│
├── docs/                       Supporting documentation and assets
├── README.md                   You are here
├── SETUP.md                    Step-by-step install guide
├── License.md
└── .gitignore
```

---

## 🧱 Backend architecture

Each Django app owns one area of the domain and is mounted under a fixed prefix in `backend/config/urls.py`:

| App            | URL prefix                  | Responsibility                                                               |
| -------------- | --------------------------- | ---------------------------------------------------------------------------- |
| `api`          | `/api/`                     | Health check, login / logout / current user, profile settings                |
| `users`        | `/api/settings/`            | RBAC, user management, family management                                     |
| `portfolio`    | `/api/portfolio/`           | Assets, transactions, portfolio tree / summary / holdings, manual price edit |
| `analytics`    | `/api/analytics/`           | Wealth allocation, performance, XIRR, historical analytics                   |
| `mutual_funds` | `/api/mutual-funds/`        | Schemes, MF transactions, MF holdings, SIPs                                  |
| `market_data`  | _(internal + stock search)_ | Price providers, background price refresh                                    |
| `ai`           | `/api/ai/`                  | Portfolio chat; also mounts `portfolio_news` URLs                            |
| `investments`  | `/api/investments/`         | Excel transaction import, Security Master                                    |

**`users.permissions` is the single authorization service** — role-rank helpers, family-scope helpers (`get_visible_owner_ids`, `get_manageable_users_queryset`) and reusable DRF permission classes (`IsViewer`, `IsAdmin`, `IsSuperUser`, `IsSystemOwner`, `IsAdminOrSuperUser`). Its docstring holds the canonical permission matrix — keep it in sync with the table in this README.

---

## 🧬 Data model

```mermaid
erDiagram
    USER ||--|| USERPROFILE : "has role"
    USERPROFILE }o--o{ FAMILYGROUP : "FamilyMembership"
    USER ||--o{ USERAUDITLOG : "audit trail"

    USER ||--o{ ASSET : owns
    ASSET ||--o{ TRANSACTION : "bought / sold via"
    ASSET ||--o{ HOLDING : "derived into"
    ASSET ||--o{ MARKETPRICE : "priced by"
    ASSET }o--o| SECURITYMASTER : "linked by ISIN"

    USER ||--o{ MUTUALFUNDTRANSACTION : records
    USER ||--o{ SIP : schedules
    MUTUALFUNDSCHEME ||--o{ MUTUALFUNDNAV : "daily NAV"
    MUTUALFUNDSCHEME ||--o{ MUTUALFUNDTRANSACTION : "traded as"
    MUTUALFUNDSCHEME ||--o{ MUTUALFUNDHOLDING : "held as"
    MUTUALFUNDSCHEME ||--o{ SIP : "invested via"
    SIP ||--o{ SIPINSTALLMENT : "generates"

    NEWSARTICLE ||--o{ NEWSARTICLESOURCE : "seen at"
    NEWSARTICLE ||--o{ PORTFOLIONEWSALERT : "raises"
    USER ||--o{ PORTFOLIONEWSALERT : receives
    HOLDING ||--o{ PORTFOLIONEWSALERT : "matched to"
```

_A simplified conceptual view — see each app's `models.py` for exact fields and constraints._

| App              | Models                                                                                                     | Notes                                                                                                                                                                                           |
| ---------------- | ---------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `users`          | `UserProfile`, `FamilyGroup`, `FamilyMembership`, `UserAuditLog`                                           | `UserProfile` holds role and active family; the audit log is append-only                                                                                                                        |
| `investments`    | `Asset`, `Transaction`, `Holding`, `SecurityMaster`                                                        | `Transaction` is the source of truth for quantity and invested value; `Holding` is derived and rebuildable. Generic `Transaction` rows can also contain legacy/imported mutual-fund transactions. `SecurityMaster` stores sector, cap-type, AMC name, P/E, P/B, ROE and credit rating |
| `market_data`    | `MarketPrice`                                                                                              | Tagged by source: **Yahoo Finance**, **AMFI** or **Manual**                                                                                                                                     |
| `mutual_funds`   | `MutualFundScheme`, `MutualFundNAV`, `MutualFundTransaction`, `MutualFundHolding`, `AMFIMasterScheme`, `AMFIMasterNAV`, `SIP`, `SIPInstallment` | Dedicated mutual-fund models plus the persisted AMFI master scheme/NAV history used for current data, Watch List benchmarks and historical MIS valuation of legacy mutual-fund holdings |
| `portfolio_news` | `NewsArticle`, `NewsArticleSource`, `PortfolioNewsAlert`                                                   | Alerts are unique per user / article / holding                                                                                                                                                  |
| `ai`             | `GeminiUsageLog`                                                                                           | Token usage per Gemini call — Portfolio Chat only                                                                                                                                         |

---

## Dashboard and Analytics calculation ownership

Dashboard and Analytics use backend view models as the authoritative financial/business calculation layer. Django owns valuation, P&L, XIRR, allocation, historical valuation, standard-allocation targets, advisor metrics, market-cap/sector analytics, performance aggregation, and page-level insights. Angular consumes the aggregated view model and is responsible for presentation, formatting, filters, interaction state, and chart rendering.

The page-level Dashboard/Analytics response is available at `GET /api/analytics/wealth/dashboard/` with `days` for rolling periods, `period=this-month|last-month|inception`, and an optional `family` filter. Existing granular wealth endpoints remain available for other consumers and are not removed by this refactor.

## 🔌 API reference

All endpoints require an authenticated Django session unless noted. Auth uses **session cookie + CSRF token** (no bearer tokens).

<details open>
<summary><b>Auth and profile</b> — <code>/api/</code></summary>

| Method | Endpoint                         | Purpose                      |
| ------ | -------------------------------- | ---------------------------- |
| `GET`  | `/api/health/`                   | Health check _(no auth)_     |
| `POST` | `/api/auth/login/`               | Log in (starts a session)    |
| `POST` | `/api/auth/logout/`              | Log out                      |
| `GET`  | `/api/auth/me/`                  | Current user                 |
| `GET`  | `/api/settings/`                 | Account settings             |
| `POST` | `/api/settings/update/`          | Update profile / preferences |
| `POST` | `/api/settings/change-password/` | Change own password          |

</details>

<details>
<summary><b>RBAC, users, families and manual prices</b> — <code>/api/settings/</code></summary>

| Method                       | Endpoint                                         | Purpose                              |
| ---------------------------- | ------------------------------------------------ | ------------------------------------ |
| `GET`                        | `/api/settings/me/`                              | Current user's role and family state |
| `POST`                       | `/api/settings/me/active-family/`                | Switch the active family             |
| `GET` `POST`                 | `/api/settings/users/`                           | List / create users (role-scoped)    |
| `GET` `PUT` `PATCH` `DELETE` | `/api/settings/users/<id>/`                      | Retrieve / update / delete a user    |
| `POST`                       | `/api/settings/users/<id>/activate/`             | Activate a user                      |
| `POST`                       | `/api/settings/users/<id>/deactivate/`           | Deactivate a user                    |
| `POST`                       | `/api/settings/users/<id>/reset-password/`       | Reset a user's password              |
| `GET` `POST`                 | `/api/settings/groups/`                          | List / create families               |
| `PATCH` `DELETE`             | `/api/settings/groups/<id>/`                     | Update / delete a family             |
| `POST` `DELETE`              | `/api/settings/groups/<id>/members/[<user_id>/]` | Add / remove a family member         |
| `GET`                        | `/api/settings/prices/`                          | List overridable prices              |
| `PUT` `PATCH` `DELETE`       | `/api/settings/prices/<asset_id>/`               | Set / clear a manual price           |
| `GET`                        | `/api/settings/tax-rates/`                      | List asset-name tax settings         |
| `GET`                        | `/api/settings/tax-rates/history/`              | Tax-setting change history            |

</details>

<details>
<summary><b>Portfolio</b> — <code>/api/portfolio/</code></summary>

| Method                       | Endpoint                                   | Purpose                                  |
| ---------------------------- | ------------------------------------------ | ---------------------------------------- |
| `GET` `POST`                 | `/api/portfolio/assets/`                   | List / create assets                     |
| `GET` `PUT` `PATCH` `DELETE` | `/api/portfolio/assets/<id>/`              | Retrieve / update / delete an asset      |
| `GET` `POST`                 | `/api/portfolio/transactions/`             | List / create transactions               |
| `GET` `PUT` `PATCH` `DELETE` | `/api/portfolio/transactions/<id>/`        | Retrieve / update / delete a transaction |
| `GET`                        | `/api/portfolio/transactions/edit-history/` | Transaction edit and deletion audit history |
| `GET`                        | `/api/portfolio/summary/`                  | Portfolio summary                        |
| `GET`                        | `/api/portfolio/holdings/`                 | Holdings                                 |
| `GET`                        | `/api/portfolio/underlying-uploads/`       | Latest underlying upload snapshot per asset |
| `GET`                        | `/api/portfolio/underlying-template/`      | Download sample underlying Excel format   |
| `POST`                       | `/api/portfolio/assets/<id>/underlying/import/` | Upload underlying holdings for an asset |
| `GET`                        | `/api/portfolio/tree/`                     | Hierarchical portfolio tree              |
| `PUT` `PATCH` `DELETE`       | `/api/portfolio/assets/<id>/manual-price/` | Manual price override                    |
| `GET`                        | `/api/portfolio/mis-report/`              | MIS Report data                           |
| `GET`                        | `/api/portfolio/mis-report/download/`    | Download MIS Report Excel                |
| `GET`                        | `/api/portfolio/mis-report/notes/`       | MIS Notes                                 |
| `GET`                        | `/api/portfolio/mis-report/notes/history/` | MIS Notes edit history                 |

</details>

<details>
<summary><b>Analytics</b> — <code>/api/analytics/</code></summary>

| Method | Endpoint                                       |
| ------ | ---------------------------------------------- |
| `GET`  | `/api/analytics/wealth/summary/`               |
| `GET`  | `/api/analytics/wealth/allocation/`            |
| `GET`  | `/api/analytics/wealth/performance/`           |
| `GET`  | `/api/analytics/wealth/xirr/`                  |
| `GET`  | `/api/analytics/wealth/investment-summary/`    |
| `GET`  | `/api/analytics/wealth/standard-allocations/`  | Read global/family Standard Allocation        |
| `POST` | `/api/analytics/wealth/standard-allocations/update/` | Save global/family Standard Allocation |
| `GET`  | `/api/analytics/wealth/sector-allocation/`     |
| `GET`  | `/api/analytics/wealth/market-cap-allocation/` |
| `GET`  | `/api/analytics/wealth/equity-analysis/`       |
| `GET`  | `/api/analytics/wealth/fixed-income-analysis/` |
| `GET`  | `/api/analytics/wealth/historical/`            |

</details>

<details>
<summary><b>Mutual funds and SIPs</b> — <code>/api/mutual-funds/</code></summary>

| Method | Endpoint                                           | Purpose                  |
| ------ | -------------------------------------------------- | ------------------------ |
| `GET`  | `/api/mutual-funds/summary/`                       | MF summary               |
| `GET`  | `/api/mutual-funds/holdings/`                      | MF holdings              |
| `GET`  | `/api/mutual-funds/transactions/`                  | MF transactions          |
| `POST` | `/api/mutual-funds/transactions/create/`           | Record an MF transaction |
| `GET`  | `/api/mutual-funds/schemes/`                       | Scheme list              |
| `GET`  | `/api/mutual-funds/sips/`                          | SIPs                     |
| `GET`  | `/api/mutual-funds/sips/due/`                      | Due / overdue SIPs       |
| `POST` | `/api/mutual-funds/sips/create/`                   | Create a SIP             |
| `POST` | `/api/mutual-funds/sip-installments/<id>/execute/` | Execute one installment  |

</details>

<details>
<summary><b>Investments, AI and news</b> — <code>/api/investments/</code>, <code>/api/ai/</code></summary>

| Method        | Endpoint                                 | Purpose                               |
| ------------- | ---------------------------------------- | ------------------------------------- |
| `POST`        | `/api/investments/import/`               | Import transactions from Excel        |
| `GET`         | `/api/investments/security-master/`      | Security Master list                  |
| `GET` `PATCH` | `/api/investments/security-master/<id>/` | Retrieve / edit a Security Master row |
| `POST`        | `/api/ai/chat/`                          | Portfolio chat (Gemini)               |
| `GET`         | `/api/ai/news/`                          | Portfolio news alerts                 |
| `GET`         | `/api/ai/notifications/`                 | Notification feed                     |
| `POST`        | `/api/ai/notifications/<alert_id>/read/` | Mark an alert as read                 |

</details>

---

## 🎨 Frontend architecture

- **Standalone Angular components** throughout — no `NgModule`s.
- `core/services/rbac.service.ts` is the single source of role / permission / family state. It hides controls, but every action is still independently authorized by the backend.
- **One API client service per backend area** under `core/services/`, each reading its base URL from `environment.apiUrl` — nothing hardcodes a host.
- `core/services/browser-notification.service.ts` manages browser notification permission and Web Push subscriptions for **Critical / High** news alerts.
- `frontend/public/push-sw.js` is the service worker that receives push events and displays notifications even when the Angular page is closed.
- The backend stores browser subscriptions in `PushSubscription` and sends qualifying alerts through VAPID/Web Push when `WEB_PUSH_ENABLED=True`.
- `auth.guard` protects every route and loads the RBAC role before rendering.

| Route             | Screen                                                        |
| ----------------- | ------------------------------------------------------------- |
| `/login`          | Sign in                                                       |
| `/dashboard`      | Net worth, allocation, investment summary                     |
| `/portfolio`      | Holdings tree, transactions, import, manual price override    |
| `/mis-report`     | IPS, Data Sheet, Fund Type-wise Summary, Excel download       |
| `/analytics`      | Allocation, performance, XIRR, historical wealth              |
| `/reports`        | Excel / PDF exports                                           |
| `/ai-chat`        | Gemini portfolio assistant                                    |
| `/portfolio-news` | News alerts for your holdings                                 |
| `/settings`       | Account · Security · Tax Settings · Logs · User Management · Family Management |

All authenticated routes sit under a `ShellComponent` (sidebar + header with the family switcher).

---

## ⏰ Background jobs

PWMS has two layers of automatic refresh:

1. **In-process jobs** run while Django is running. They handle the normal application background work, including post-import refreshes, the daily refresh pipeline and Portfolio News monitoring.
2. **Windows Task Scheduler** can run the market-price command independently of the Django server process. This is the recommended Windows mechanism for keeping Stock / ETF / Bond / SGB prices refreshed automatically.

| #   | Job / mechanism              | Cadence                                      | What it does |
| --- | ---------------------------- | -------------------------------------------- | ------------ |
| 1   | **Market price refresh**     | **Every hour on Windows**                    | Runs `update_market_prices` for Stock, ETF, Bond and SGB prices. ETFs use AMFI first when covered, with Yahoo fallback. |
| 2   | **In-process price refresh** | While Django is running                      | Existing application-level price refresh mechanism; PostgreSQL advisory locks prevent overlapping refresh work. |
| 3   | **Daily refresh**             | Once per calendar day of uptime              | AMFI NAV, security-master ratios, benchmark master coverage, SIP sync and execute. |
| 4   | **Post-import refresh**       | Right after an import commits                | Immediate price refresh for newly added assets. |
| 5   | **Portfolio News monitor**    | Every `NEWS_MONITOR_INTERVAL` (default 30 min) | Full news discovery → match → analyze → alert pass for every user. |

### Windows automatic market-price refresh

The repository includes a Windows Task Scheduler setup so market prices can continue refreshing even when the Django development server is not running.

From the repository root in PowerShell:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\windows\register_market_price_scheduler.ps1
```

This creates the scheduled task `PWMS-Market-Price-Refresh`.

The task:

- runs `backend\venv\Scripts\python.exe manage.py update_market_prices`
- runs once every hour
- starts shortly after registration
- uses `-StartWhenAvailable` so a missed run can start when Windows becomes available
- prevents overlapping instances
- writes execution output to `backend\logs\scheduled_market_price_refresh.log`

Verify the task:

```powershell
Get-ScheduledTask -TaskName "PWMS-Market-Price-Refresh"
Get-ScheduledTaskInfo -TaskName "PWMS-Market-Price-Refresh"
```

Remove it with:

```powershell
Unregister-ScheduledTask -TaskName "PWMS-Market-Price-Refresh" -Confirm:$false
```

The setup is documented further in [`scripts/windows/README.md`](./scripts/windows/README.md).

The daily refresh also runs the **benchmark master coverage check** for Nifty 50 and BSE 500 before the normal daily refresh. The BSE 500 loader only reads the configured local source when it is a regular file, so a directory or invalid file-path setting does not cause the scheduler to fail; supported remote/fallback benchmark sources remain available.

---

## 🔔 Background Web Push notifications

PWMS supports **Web Push notifications** for newly created **Critical** and **High** Portfolio News alerts. The browser registers `/push-sw.js`, stores its Push API subscription in Django, and the backend sends a push message using VAPID credentials.

### Setup

From `backend/` with the virtual environment active:

```powershell
python manage.py migrate
python manage.py generate_web_push_keys
```

Copy the generated values into your local `backend/.env`:

```ini
WEB_PUSH_VAPID_PUBLIC_KEY=<generated-public-key>
WEB_PUSH_VAPID_PRIVATE_KEY=<generated-private-key>
WEB_PUSH_VAPID_SUBJECT=mailto:your-email@example.com
```

Restart Django after changing `.env`.

> **Security:** the public VAPID key is sent to the browser and is safe to expose. The private VAPID key is a server secret and must never be committed to Git or exposed to frontend code.

### Browser subscription

1. Start the Django backend and Angular frontend.
2. Open `http://localhost:4200` in a supported browser.
3. Sign in and open the notification center / Portfolio News area.
4. Grant browser notification permission when prompted.
5. The Angular service registers `/push-sw.js`, creates a Push API subscription, and sends that subscription to Django.
6. Django stores the subscription in `portfolio_news.PushSubscription`.

Verify from the Django shell:

```python
from portfolio_news.models import PushSubscription
PushSubscription.objects.all().values("user_id", "endpoint", "enabled")
```

### End-to-end background test

After the subscription exists:

1. Leave the browser running but close the PWMS tab.
2. Trigger or ingest a new qualifying **Critical / High** Portfolio News alert.
3. Confirm the operating system/browser displays the notification while the Angular page is closed.
4. Re-open PWMS and verify the alert is also present in the in-app notification feed.
5. Check `PortfolioNewsAlert.notification_sent` if you need to confirm that the backend recorded a successful push delivery.

The backend disables stale subscriptions when a push provider returns HTTP **404** or **410**.

### Troubleshooting

- **No permission prompt:** check the browser's site notification settings and make sure the browser supports Notifications, Service Workers and Push API.
- **No service worker:** check browser DevTools → **Application → Service Workers** and confirm `/push-sw.js` is registered.
- **Subscription missing:** confirm `WEB_PUSH_VAPID_PUBLIC_KEY` and `WEB_PUSH_VAPID_PRIVATE_KEY` are both configured and restart Django.
- **Alert appears in PWMS but no popup:** verify the alert is newly created and has **Critical** or **High** notification tier; lower tiers are intentionally not pushed.
- **Production deployment:** serve the frontend over **HTTPS**. Do not use the development `ng serve` server as an internet-facing production server.

See [SETUP.md](./SETUP.md#84-background-web-push-notifications) for the full setup and troubleshooting checklist.

---

## 🚀 Deployment and refresh performance

The deployment refresh path includes protections for production-style multi-worker environments and avoids unnecessary market-data work. Benchmark history and Watch List chart preparation also validate data density before accepting historical series.

- **PostgreSQL advisory locks** prevent concurrent scheduled/manual market refresh workers from performing the same refresh simultaneously.
- Advisory locking is applied to the scheduled refresh command, one-off market-price updates, and the market-price scheduler.
- **PostgreSQL connection reuse** uses `CONN_MAX_AGE` through `POSTGRES_CONN_MAX_AGE`, with Django connection health checks enabled.
- **Current-data reuse** avoids recreating an existing holding/data record when the required market data is already current.
- **Historical Yahoo Finance writes** use bulk upsert-style persistence rather than one database operation per historical price row.
- **Angular route preloading** uses `PreloadAllModules` to reduce navigation latency after the application starts.
- These changes do not introduce Celery, Redis, cron, or a new background-job architecture; they harden the existing in-process refresh flow.

> **PostgreSQL note:** advisory-lock coordination is available when the application is running against PostgreSQL. SQLite remains supported for local development, but it does not provide PostgreSQL advisory-lock semantics.

---

## 🔧 Configuration

Settings are split into two layers: **secrets and deployment-specific values** come from `backend/.env` (template: [`backend/.env.example`](./backend/.env.example)), while **non-secret application/provider settings** are centralized in [`backend/config/pwms_config.json`](./backend/config/pwms_config.json). The Python loader [`backend/config/pwms_config.py`](./backend/config/pwms_config.py) reads that JSON and supports the optional `PWMS_CONFIG_OVERRIDES` environment variable for deployment-specific JSON overrides. `SECRET_KEY` is required when `DEBUG=False`; when `DEBUG=True`, Django generates a development-only random key at startup.

| Variable                                                                                  | Purpose                                                     | Local default                               | Production / template value                                                                                   |
| ----------------------------------------------------------------------------------------- | ----------------------------------------------------------- | ------------------------------------------- | ------------------------------------------------------------------------------------------------------------- |
| `SECRET_KEY`                                                                              | Django's cryptographic signing key                          | Generated automatically only when `DEBUG=True` | Required when `DEBUG=False`; random key — never committed                                                     |
| `DEBUG`                                                                                   | Debug mode                                                  | `False`                                     | `False`                                                                                                       |
| `ALLOWED_HOSTS`                                                                           | Comma-separated allowed hosts                               | _(empty)_                                   | Your domain(s) / IP(s) — Django rejects everything else once `DEBUG=False`                                    |
| `CORS_ALLOWED_ORIGINS`                                                                    | Allowed frontend origins                                    | `http://localhost:4200`                     | Exact `https://` origin of the frontend                                                                       |
| `CSRF_TRUSTED_ORIGINS`                                                                    | Trusted origins for CSRF                                    | `http://localhost:4200`                     | Same as above                                                                                                 |
| `SESSION_COOKIE_SECURE`                                                                   | Require HTTPS for the session cookie                        | `False`                                     | `True` — only once real HTTPS is in place                                                                     |
| `CSRF_COOKIE_SECURE`                                                                      | Require HTTPS for the CSRF cookie                           | `False`                                     | `True` — only once real HTTPS is in place                                                                     |
| `SECURE_SSL_REDIRECT`                                                                     | Redirect HTTP → HTTPS                                       | `False`                                     | `True`                                                                                                        |
| `DATABASE_ENGINE`                                                                         | `sqlite` or `postgresql`                                    | `sqlite`                                    | `sqlite` until your PostgreSQL migration is validated                                                         |
| `POSTGRES_DB` · `POSTGRES_USER` · `POSTGRES_PASSWORD` · `POSTGRES_HOST` · `POSTGRES_PORT` | PostgreSQL connection                                       | used only when `DATABASE_ENGINE=postgresql` | template: `pwms` · `pwms_user` · _(set a password)_ · `localhost` · `5432`                                    |
| `POSTGRES_CONN_MAX_AGE`                                                                   | Persistent PostgreSQL connection lifetime                    | `60` seconds when PostgreSQL is enabled       | Tune for the deployment; database health checks remain enabled                                                    |
| `GEMINI_API_KEY` _(or `GOOGLE_API_KEY`)_                                                  | Enables Portfolio Chat only                                  | —                                           | Optional; Portfolio News does not use this key |
| `NEWS_CORPORATE_FILINGS_ENABLED` | Enable corporate filing ingestion in the scheduler | `True` | Enabled by default; disable only when filing monitoring is not required |
| `NEWS_NSE_FILINGS_ENABLED` | Enable NSE filing adapter | `True` | Applies only when corporate filing intelligence is enabled |
| `NEWS_BSE_FILINGS_ENABLED` | Enable BSE filing adapter | `True` | Applies only when corporate filing intelligence is enabled |
| `EXCHANGE_FILING_FEED_URL_NSE` | Optional configured NSE HTTP/CSV/JSON feed or mirror | — | The built-in NSE public Corporate Announcements adapter is used when this is not set |
| `EXCHANGE_FILING_FEED_URL_BSE` | Authorized BSE HTTP/CSV/JSON feed or mirror | — | BSE ingestion requires a configured/authorized feed; an unconfigured BSE provider is skipped |
| `NEWS_EVENT_CLUSTER_WINDOW` | Recent event-clustering window in days | `3` | Conservative cross-source clustering |
| `NEWS_NOTIFICATION_COOLDOWN` | Maximum age for retrying an unsent immediate notification | `86400` | Prevents stale push retries; does not suppress new events |
| `NEWS_MONITOR_INTERVAL`                                                                   | Seconds between automatic news runs                         | `1800`                                      | Tune as needed                                                                                                |
| `NEWS_MONITOR_ANALYSIS_DELAY_SECONDS`                                                      | Optional pause between local analysis batches              | `0`                                         | Usually leave at `0`; no external AI rate limit exists                                                            |
| `WATCHLIST_PMS_SOURCE_URLS`                                                               | Optional comma-separated authoritative PMS source endpoints | _(blank)_                                   | Leave blank when no reliable source exists — no PMS values are ever fabricated                                |
| `WEB_PUSH_VAPID_PUBLIC_KEY`                                                                | Browser Web Push public VAPID key                          | _(blank)_                                   | Generate with `python manage.py generate_web_push_keys`                                                      |
| `WEB_PUSH_VAPID_PRIVATE_KEY`                                                               | Browser Web Push private VAPID key                         | _(blank)_                                   | Keep secret; never commit it                                                                                   |
| `WEB_PUSH_VAPID_SUBJECT`                                                                   | VAPID contact / application subject                        | `mailto:admin@example.com`                | Use a real maintainer email or HTTPS subject                                                                    |

> ⚠️ **Turn the `*_SECURE` flags and `SECURE_SSL_REDIRECT` on only after HTTPS is working.** Browsers refuse `Secure` cookies over plain HTTP, so enabling them early breaks login.

**Frontend:** the backend URL lives in `frontend/src/environments/environment.ts` for development (`http://localhost:8000`). The production environment currently uses an empty `apiUrl`, so production builds expect `/api` to be routed by the same-origin reverse proxy. If your deployment uses a separate backend origin, set `frontend/src/environments/environment.prod.ts` before building.

---

## 📜 Management commands

Run from `backend/` with the virtual environment active: `python manage.py <command>`.

| Command                          | App              | What it does                                                                 |
| -------------------------------- | ---------------- | ---------------------------------------------------------------------------- |
| `update_market_prices`           | `market_data`    | One-off price refresh; on Windows it can also be run automatically every hour by the documented Task Scheduler setup |
| `monitor_portfolio_news`         | `portfolio_news` | One full news-monitoring pass for every user (also automatic)                |
| `ingest_exchange_filings`         | `filing_intelligence` | Ingest corporate filings for a selected exchange; supports `--exchange nse|bse`, `--hours` and dry-run workflows |
| `diagnose_corporate_filings`      | `filing_intelligence` | Read-only diagnostics for filing identifier coverage, portfolio matches, holding-user matches, watchlists and existing filing alerts |
| `gemini_usage`                   | `ai`             | Summary of Gemini token usage                                                |
| `fetch_amfi_nav`                 | `mutual_funds`   | Import latest AMFI NAV master, or historical AMFI master data with `--from-date` / `--to-date` |
| `execute_sips`                   | `mutual_funds`   | Execute all due SIP installments for a user                                  |
| `rebuild_holdings`               | `portfolio`      | Rebuild holdings from transactions — `--user-id <id>`                        |
| `load_security_master_data`      | `investments`    | Load researched sector / cap-type / P/E / P/B / ROE data into SecurityMaster |
| `link_security_master`           | `investments`    | Link Assets to their SecurityMaster row by ISIN _(dry-run by default)_       |
| `import_amfi_cap_classification` | `investments`    | Classify stocks Large / Mid / Small Cap by AMFI rank _(dry-run by default)_  |
| `generate_web_push_keys`           | `portfolio_news` | Generate URL-safe VAPID keys for browser Web Push |
| `run_scheduled_refresh`           | `market_data`    | Run the scheduled market-price/security/news refresh pipeline; investment-driven AMFI refresh and MIS history are handled by the separate 30-minute in-process scheduler |
Standard Django commands you'll use as well: `migrate`, `check`, `createsuperuser`, `changepassword <username>`, `shell`, `test`.

> Tip: run any command with `--help` to see its options — including how to apply the _dry-run by default_ commands.

---

## 🔒 Security model and privacy

**Security**

- **Session auth + CSRF** — no long-lived bearer tokens to leak.
- **Server-side authorization on every request.** Role and family memberships are re-read from the database each time; a client-claimed role or family ID is never trusted.
- **Privilege-escalation guards** — nobody can change their own role; the last active System Owner cannot be demoted; missing profiles never grant access.
- **Audit trail** — user-management activity is written to an append-only audit log, and every manual price override is audit-logged (who, when, from what).
- **Secrets stay out of git** — `.env` is gitignored; only `.env.example` is committed.
- **Tested** — `users/tests.py` covers every role × capability combination and privilege-escalation attempt.

**What leaves your machine**

PWMS is self-hosted, so your database never leaves your server. The following outbound calls do happen:

| Destination         | When                      | What is sent                                                                                                            |
| ------------------- | ------------------------- | ----------------------------------------------------------------------------------------------------------------------- |
| **Yahoo Finance**   | Price refresh             | Ticker symbols for your Stock / ETF holdings                                                                            |
| **AMFI**            | NAV refresh               | Nothing personal — a public NAV file is downloaded                                                                      |
| **Google News RSS** | News monitor              | Search queries built from the securities you hold                                                                       |
| **Google Gemini**   | AI Chat only | Structured portfolio context is sent only when you explicitly configure a Gemini key. |

Prefer to keep everything local? Leave `GEMINI_API_KEY` unset — Portfolio News remains fully functional; only Portfolio Chat is unavailable.

---

## ✅ Testing

**Backend** — from `backend/` with the virtual environment active:

```bash
python manage.py test                       # everything
python manage.py test users portfolio -v 2  # RBAC + portfolio, verbose
python manage.py test portfolio_news.test_web_push -v 2 # Web Push delivery behavior
```

| Suite                   | Covers                                                                |
| ----------------------- | --------------------------------------------------------------------- |
| `users/tests.py`        | Every role × capability combination and privilege-escalation attempts |
| `mutual_funds/tests.py` | Batched AMFI NAV import                                               |
| `investments/tests.py`  | The transaction importer and AMC-name / quant auto-enrichment         |
| Transaction upload workflow | Upload audit/history, standard template endpoints, row-level failure handling, and transaction import behavior |
| `portfolio_news/test_web_push.py` | VAPID/Web Push delivery, subscription handling and notification_sent semantics |
| `portfolio/test_mis_report.py` | MIS Report API, historical valuation, Excel structure, display units, FIFO taxation, negative-loss tax benefits, and family authorization |
| `analytics/test_historical_wealth.py` | Historical wealth regression coverage, including legacy mutual-fund classification and AMFI master NAV valuation |
| `filing_intelligence` | Corporate filing classifier, event precedence, material-change detection, deterministic portfolio matching, filing ingestion, deduplication, cross-source clustering and alert creation. Current branch verification: **20 tests, all passing** |

**Frontend** — from `frontend/`:

```bash
npm test          # unit tests
npm run build     # verifies the whole app compiles
```

---

## 🚧 Known limitations

| Limitation                                   | Details                                                                                                                                                                                                 |
| -------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **SQLite is the default database**           | WAL mode + busy-timeout reduce — but do not eliminate — write contention under concurrent load, and there is no automated backup yet. Fine for a household; for many concurrent writers use PostgreSQL. |
| **Windows market-price scheduling is OS-level** | Windows automatic market-price refresh uses Task Scheduler to invoke `update_market_prices` hourly. The in-process application jobs remain separate; PostgreSQL advisory locks prevent overlapping refresh work when PostgreSQL is used. |
| **Single owning user per record**            | `Asset` / `Transaction` are stored against one owning `User`; family sharing is a visibility layer on top.                                                                                              |
| **Web Push requires VAPID configuration**     | Background browser delivery requires `WEB_PUSH_VAPID_PUBLIC_KEY`, `WEB_PUSH_VAPID_PRIVATE_KEY` and `WEB_PUSH_VAPID_SUBJECT`. Without them, news alerts still appear in the in-app notification feed but no push is sent. |
| **Browser permission is required**             | The user must grant notification permission and allow the service worker to subscribe. |
| **HTTPS is required outside localhost**        | Service workers and Push API require a secure context in production. Localhost is suitable for development. |
| **Some SIP tests drift with the calendar**   | A few SIP-scheduling tests compare against today's real date; this is a known fixture limitation that does not affect the running app.                                                                  |
| **Third-party data can lag or fail**         | Yahoo Finance, AMFI and Google News are external sources; use **manual prices** when a quote is missing.                                                                                                |
| **AMFI refresh is investment-driven**       | The AMFI feed is downloaded as a complete public source file, but PWMS persists and processes only records matching held investment ISINs. Mutual-fund history is persisted locally for MIS. |
| **MIS valuation depends on available market data** | Historical MIS valuation uses the latest available market price / NAV on or before the requested valuation date; missing valuation data may result in an unavailable market-value figure. |
| **Corporate filing coverage depends on exchange feeds** | NSE uses the built-in public Corporate Announcements adapter; BSE requires an authorized/configured feed. Unconfigured BSE ingestion is skipped rather than treated as a provider failure. |
| **Deterministic news is intentionally not LLM-generated** | Portfolio News classifications, filing categories, scoring and notification tiers use transparent rules. This avoids hosted AI cost but cannot provide open-ended semantic reasoning. |

See [SETUP.md](./SETUP.md) for what to configure before any real deployment (`.env`, `environment.prod.ts` and the WSGI / ASGI options).

---

## 🤝 Contributing

Found a bug or have an idea? **Open an issue** on the [issue tracker](https://github.com/avviiiral/Personal_Wealth_Monitoring_System/issues). If you're proposing a change:

1. Create a feature branch from `main`.
2. Run `python manage.py test` (backend) and `npm run build` (frontend) before pushing.
3. If you touch roles or capabilities, update **both** the matrix in `backend/users/permissions.py` and the table in this README.
4. Keep every new endpoint behind the central permission classes — never trust client-supplied roles or family IDs.

---

## 📄 License and disclaimer

Released under a **proprietary license** — see [`License.md`](./License.md).

> **Disclaimer.** PWMS is a personal record-keeping and analytics tool. It is **not** investment, tax or legal advice. Prices and NAVs come from third-party sources and may be delayed, adjusted or unavailable, and AI-generated commentary can be wrong. Always reconcile against your broker, depository and AMC statements before making decisions. PWMS is not affiliated with Yahoo, AMFI, Google or any exchange.

---

<div align="center">

**Built by [@avviiiral](https://github.com/avviiiral)**

[⬆ Back to top](#-personal-wealth-monitoring-system)

</div>
