# PWMS Exchange Filing Intelligence

Exchange filing intelligence is integrated into the existing Portfolio News alert and notification path. Filings are stored separately, then matched to existing investments.Asset records, portfolio holdings, and existing Watch List entries before an existing PortfolioNewsAlert is created.

## Official source verification

As of 3 October 2026, the exchanges expose corporate filing information through their official sites/data products:

- NSE: the official Corporate Filings / Announcements pages expose current announcements and a CSV download control. NSE also documents an End of Day Corporate Announcement data product delivered via SFTP.
- BSE: the official BSE market-data portal lists Corporate Data with API delivery covering corporate announcements, while BSE's information-products documentation describes corporate announcements as a subscribed data product.

Production source links: NSE Corporate Filings / Announcements: https://www.nseindia.com/companies-listing/corporate-filings-announcements ; NSE Corporate Data subscription: https://www.nseindia.com/static/market-data/corporate-data-subscription ; BSE Market Data / Self Data Feed: https://marketdata.bseindia.com/ ; BSE Information Products: https://www.bseindia.com/downloads1/Information_Products_Pricing_Sheet.pdf

The deployment consequence is that PWMS should not hard-code a reverse-engineered NSE/BSE website API and should not bypass CAPTCHA, anti-bot controls, authentication, or other exchange protections. The production feed must be one that the deployment is authorized to consume.

### NSE

NSE's current official Corporate Data page documents an End of Day Corporate Announcement product delivered through SFTP after 8:00 PM IST. The current PWMS provider is deliberately HTTP/CSV/JSON based, so use an authorized HTTP(S) mirror/gateway that receives the NSE licensed feed, or add a dedicated SFTP adapter once the exact NSE feed specification and credentials are available.

Do not substitute an unofficial nseindia.com API endpoint for the licensed feed in production. The public Corporate Filings page is useful for manual verification, but that is different from a supported production data contract.

### BSE

BSE's official data-feed materials identify Corporate Data as an API product containing corporate announcements. The BSE portal directs customers through registration/KYC and data-plan selection. Production PWMS configuration should therefore use the API/feed endpoint and credentials supplied under the BSE data agreement.

The repository's provider already accepts JSON or CSV records, so no alternate alert/matching architecture is required.

## Provider boundary

NSE and BSE expose corporate filing/data products through official exchange channels. PWMS deliberately does not scrape protected endpoints, bypass CAPTCHA/anti-bot controls, or hard-code undocumented exchange APIs. Production ingestion therefore uses exchange-approved/configured feed endpoints:

- EXCHANGE_FILING_FEED_URL_NSE
- EXCHANGE_FILING_FEED_URL_BSE

The provider accepts JSON or CSV records with common field names. If an exchange feed is unavailable, that exchange is logged as a provider failure and the other exchange continues.

## Production configuration

Add the approved feed URLs to backend/.env:

    EXCHANGE_FILING_FEED_URL_NSE=<approved-NSE-HTTP-feed-or-authorized-mirror>
    EXCHANGE_FILING_FEED_URL_BSE=<approved-BSE-API-or-feed-endpoint>
    NEWS_CORPORATE_FILINGS_ENABLED=True
NEWS_NSE_FILINGS_ENABLED=True
NEWS_BSE_FILINGS_ENABLED=True

The placeholders are intentional. There is no single universally valid public production URL that PWMS can safely claim for both exchanges without the corresponding exchange data entitlement/feed contract.

After configuring the feeds:

    cd D:\PWMS\Personal_Wealth_Monitoring_System\backend
    python manage.py check
    python manage.py ingest_exchange_filings --exchange=nse --hours=24 --dry-run
    python manage.py ingest_exchange_filings --exchange=bse --hours=24 --dry-run
    python manage.py ingest_exchange_filings --hours=24

Expected dry-run behavior: filings_fetched > 0 when the configured feed contains filings in the requested window; filings_stored = 0 because dry-run does not persist records; classification and matching are exercised without creating Filing or PortfolioNewsAlert rows.

For the real run, filings_stored and alerts_created may be greater than zero when filings match securities in the user's portfolio/watchlist.

## Processing flow

Provider -> normalization -> exchange/external-id or content-hash deduplication -> security matching -> portfolio/watchlist matching -> deterministic rule engine -> severity -> existing PortfolioNewsAlert -> existing notification bell and Portfolio News UI.

Unmatched filings are retained with UNMATCHED processing status.

## Rule engine

The rule engine is deterministic and contextual. It distinguishes confirmed events from phrases such as risk of fraud and alleged fraud. Severity values are INFO, LOW, MEDIUM, HIGH and CRITICAL. The classification reason is stored with the filing so the result is auditable.

Filing alerts are factual. The UI explicitly identifies them as EXCHANGE FILING and does not generate buy/sell or price-prediction claims.

## Commands

    python manage.py ingest_exchange_filings --hours=24
    python manage.py ingest_exchange_filings --exchange=nse --hours=24 --dry-run

Dry-run performs provider fetch and classification without creating Filing or PortfolioNewsAlert rows.

## Background processing

The existing PortfolioNewsScheduler remains the scheduler. Set NEWS_CORPORATE_FILINGS_ENABLED=true and configure the provider feeds; filing ingestion then runs in the same scheduler cycle as Portfolio News. No second scheduler is introduced.

## AI

No Gemini/OpenAI key is required. The deterministic rule engine is the production default. A future FilingAnalysisProvider can be inserted for ambiguous/material cases only, without changing ingestion, matching, storage, or notification contracts.

## Channels

In-app/browser notification behavior is inherited from PortfolioNewsAlert. Email/WhatsApp are not fabricated or required by this implementation.
