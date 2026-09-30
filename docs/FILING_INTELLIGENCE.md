# PWMS Exchange Filing Intelligence

Exchange filing intelligence is integrated into the existing Portfolio News alert and notification path. Filings are stored separately, then matched to existing investments.Asset records, portfolio holdings, and existing Watch List entries before an existing PortfolioNewsAlert is created.

## Provider boundary
NSE and BSE expose corporate filing/data products through official exchange channels. PWMS deliberately does not scrape protected endpoints, bypass CAPTCHA/anti-bot controls, or hard-code undocumented exchange APIs. Production ingestion therefore uses exchange-approved/configured feed endpoints:

- EXCHANGE_FILING_FEED_URL_NSE
- EXCHANGE_FILING_FEED_URL_BSE

The provider accepts JSON or CSV records with common field names. If an exchange feed is unavailable, that exchange is logged as a provider failure and the other exchange continues.

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
The existing PortfolioNewsScheduler remains the scheduler. Set FILING_INTELLIGENCE_ENABLED=true and configure the provider feeds; filing ingestion then runs in the same scheduler cycle as Portfolio News. No second scheduler is introduced.

## AI
No Gemini/OpenAI key is required. The deterministic rule engine is the production default. A future FilingAnalysisProvider can be inserted for ambiguous/material cases only, without changing ingestion, matching, storage, or notification contracts.

## Channels
In-app/browser notification behavior is inherited from PortfolioNewsAlert. Email/WhatsApp are not fabricated or required by this implementation.