# PWMS News Agent Audit and Intelligence Improvements

**Base:** `main` at `69ffc67abd16afc115187665cbb219e9b3d6949a`  
**Branch:** `feature/news-agent-intelligence-improvements`

## Verified architecture

Source inspection confirmed:
- Google News RSS ingestion in `portfolio_news/services/google_news_provider.py`.
- Deterministic portfolio matching in `holding_matcher.py` and `holdings_registry.py`.
- URL/fingerprint/fuzzy/event-family clustering and source aggregation in `deduplication.py` and `article_store.py`.
- Local rule-based analysis and separate impact, source-quality, recency and portfolio-weight scoring in `rule_based_analyzer.py` and `alert_scoring.py`.
- Per-user alert creation, database uniqueness constraints and Web Push delivery/retry behavior in `notification_creation.py`, `models.py`, `web_push.py` and `pipeline.py`.
- Scheduled monitoring and diagnostic counts in `monitor_portfolio_news.py` and `portfolio_news_scheduler.py`.
- Exchange filing ingestion, deterministic identifier matching, classification and shared event clustering in `filing_intelligence`.

## Protections to preserve

- Unique constraints prevent duplicate alert rows per user/article/holding and duplicate raw match rows.
- Alerts and push subscriptions are user-scoped.
- A local deterministic analyzer avoids mandatory paid AI dependencies.
- Provider failures are logged and isolated; BSE ingestion requires a configured feed.
- Source attribution is retained in `NewsArticleSource`.
- Filing records use exchange external IDs when available and content hashes as fallback.
- Facts, interpretation and uncertainty are represented separately.
- This change introduces no schema migration.

## Findings and fixes

### 1. Tracking URL variants defeated exact-URL deduplication — medium
**Evidence:** `compute_url_hash()` previously hashed the raw URL, so tracking parameters, fragments, host case, parameter ordering and trailing slash variants could appear distinct.

**Fix:** canonicalize URLs for hashing by removing fragments and common tracking parameters, normalizing scheme/host/default ports and trailing slashes, and sorting remaining query parameters. The original URL remains stored for source attribution and navigation.

**Tests added:** tracking URL variants produce identical normalized URLs and hashes.

### 2. Event-family matching could collapse separate events — high
**Evidence:** event-family fallback accepted one shared meaningful headline token, which could be only the company name. This risks merging separate acquisitions or orders from the same company inside the configured time window.

**Fix:** fuzzy and event-family matches now require at least two meaningful shared headline tokens. Exact URL/fingerprint checks and multi-source aggregation remain unchanged.

**Tests added:** distinct acquisitions with different counterparties remain separate; two headlines with the same company and counterparty still cluster.

## Limitations and follow-up candidates

- `PortfolioNewsMatch` does not persist a dedicated match-reason/confidence field. Adding that requires schema/API design; this patch avoids a migration.
- RSS often supplies only a headline and snippet; deterministic analysis cannot independently verify claims.
- Filing ingestion depends on the available public NSE endpoint or an authorized configured feed. Live provider availability is not asserted by unit tests.
- Changes were authored through repository tools; tests and Django checks have not been run in a local runtime here. No test result is claimed as passing.

## Validation commands

From the repository root:

```powershell
cd backend
python manage.py test portfolio_news.tests
python manage.py test portfolio_news.test_notification_creation
python manage.py test filing_intelligence.tests
python manage.py check
python manage.py makemigrations --check --dry-run
git diff --check
python manage.py test
```

Run focused and full tests with the PostgreSQL configuration as well if available. SQLite-only checks do not establish PostgreSQL compatibility.
