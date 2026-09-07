# Portfolio Intelligence & News Agent

This document covers the Portfolio News Agent: how it monitors news for a
user's real holdings, turns it into scored portfolio intelligence, and
surfaces it through the API, the Angular news feed, and the existing AI
chatbot. It assumes familiarity with the general PWMS architecture (see
`docs/PWMS_Main_Branch_Project_Overview.md`).

## 1. What it does

For every active user, the agent:

1. Reads the user's **live** portfolio (equities and mutual funds) - never a
   hardcoded holdings list.
2. Builds a small set of search queries per holding: a company-name query,
   curated event-type queries (earnings, regulatory, litigation, etc.), a
   sector query, and (where defensible) a couple of macro queries.
3. Retrieves candidate articles via a pluggable `NewsProvider` (currently
   Google News RSS).
4. Filters candidates deterministically (no AI cost yet) against the
   holding's name/aliases/ticker/ISIN - or, for sector/macro queries,
   against the holding's sector.
5. Deduplicates: same URL, same normalized-title+date fingerprint, or
   near-duplicate title within a few days all collapse into one stored
   `NewsArticle`, while every distinct publisher is retained as a
   `NewsArticleSource` ("Reported by 4 sources").
6. Sends only the surviving, not-yet-processed articles to Gemini for
   structured analysis (classification, sentiment, materiality, impact,
   confidence, fact/interpretation/uncertainty, portfolio implication).
7. Computes a portfolio-weighted `alert_score` and a `notification_tier`,
   and creates a `PortfolioNewsAlert` - once per (user, article, holding),
   ever.
8. Surfaces alerts through the news feed, notification bell, a daily
   digest, and the portfolio AI chatbot.

The goal, per the product spec, is **the smallest set of high-quality,
portfolio-relevant information a serious investor would actually want to
know** - not the largest feed.

## 2. Pipeline stages

```
Portfolio Holdings
      |
Holding Intelligence (holdings_registry.py)
      |
Query Generation (query_builder.py)
      |
News Discovery (google_news_provider.py / news_provider.py)
      |
Candidate Filtering + Holding Matching (holding_matcher.py)
      |
URL / Event Deduplication (deduplication.py, article_store.py)
      |
Source Quality Evaluation (source_quality.py)
      |
AI Analysis (gemini_analyzer.py)
      |
Portfolio Impact Scoring (alert_scoring.py)
      |
Alert Creation (notification_creation.py)
      |
Notification Decision + Digest (alert_scoring.py, digest.py)
      |
User Feed / Chat (views.py, ai/services/portfolio_news_context.py)
```

Orchestrated by `services/pipeline.py:run_portfolio_news_monitor`, invoked by
the `monitor_portfolio_news` management command. Every stage isolates its
own failures (one bad query, one bad article, one bad holding, one bad user)
and logs rather than aborting the run.

### 2.1 Holding intelligence (`holdings_registry.py`)

`get_monitored_holdings(user)` builds a `MonitoredHolding` per active
position, freshly, on every run - sold-out positions simply stop appearing.
Each `MonitoredHolding` carries: holding type, ID, display name, aliases
(derived structurally, e.g. by stripping "Limited"/"Ltd" - never a
per-company hardcoded table), symbol, ISIN, AMC name, **sector** (from
`SecurityMaster.sector` for equities, or the mutual fund's `category` as the
closest available proxy for funds), current value, and portfolio weight.

### 2.2 Query generation (`query_builder.py`)

`QueryBuilder.build_queries(holding)` returns up to
`MAX_QUERIES_PER_HOLDING` (10) queries:

- 1 company-name query
- up to 6 event-type queries (`<company> earnings`, `regulatory`,
  `acquisition`, `management`, `litigation`, `order`)
- 1 ticker query (`<SYMBOL> share`), when the symbol is long enough to be
  unambiguous
- 1 sector query (`"Indian <sector> sector"`), when the holding's sector is
  known
- up to 2 macro queries, only for sectors in the curated
  `MACRO_TOPICS_BY_SECTOR` map (e.g. banking -> RBI/interest rates/bond
  yields, oil/energy -> crude oil prices, IT -> USD/INR, pharma -> USFDA)

Sector/macro queries are deliberately narrow: the spec requires "a
defensible relationship" before macro news gets attached to a holding, so
only sectors with a clear causal story get any macro queries at all - this
is not a general market-news firehose.

### 2.3 Discovery (`news_provider.py`, `google_news_provider.py`)

`NewsProvider` is an abstract interface (`search(query, from_date, to_date)
-> List[NewsArticleResult]`) so a paid financial-news API can be added later
without touching anything downstream. The only implementation today is
`GoogleNewsRSSProvider`. A provider must never raise on network/parse
failure - it logs and returns `[]`, so one bad query never aborts a run.

### 2.4 Candidate filtering / holding matching (`holding_matcher.py`)

`HoldingMatcher.is_relevant(title, description, holding, matched_query)` is
the deterministic, non-AI filter every candidate passes through before
costing anything:

- Word-boundary match against the holding's display name, aliases, or
  ticker in the title/description.
- ISIN substring match.
- **Sector/macro fallback**: a genuine macro story ("RBI raises repo rate")
  will never mention a specific company. This path only activates when
  `matched_query` is exactly the sector or macro query
  `QueryBuilder` generated *for this holding's sector* (see
  `QueryBuilder.is_sector_or_macro_query`) - i.e. the relationship was
  established deliberately at query-generation time - and even then, the
  article text must still contain the sector name or the specific macro
  term searched for. An off-topic result from a sector/macro query is still
  rejected.

### 2.5 Deduplication (`deduplication.py`, `article_store.py`)

Three checks, cheapest first: exact URL hash, exact
`fingerprint` (normalized title + publish-date bucket), then fuzzy title
similarity (`SequenceMatcher` ratio >= 0.72) within a 3-day window. A
duplicate doesn't create a second `NewsArticle` - the new publisher is
attached as an additional `NewsArticleSource` row instead, and the
article's denormalized `source_quality`/`source_count` are updated to the
best tier seen and the total count.

### 2.6 Source quality (`source_quality.py`)

`classify_source(publisher_name)` maps a publisher string to a
`SourceQualityTier` (TIER_1/2/3) via curated substring lists (Reuters,
Bloomberg, Economic Times, Moneycontrol, exchange/regulator names -> TIER_1;
other reputable business press -> TIER_2; everything else -> TIER_3).
Override or extend without touching the code via the
`NEWS_SOURCE_QUALITY_OVERRIDES` Django setting (a `{substring: tier}` dict).

### 2.7 AI analysis (`gemini_analyzer.py`)

`GeminiArticleAnalyzer.analyze(article, holding)` sends one article +
holding pair to Gemini with a strict JSON response schema and returns a
validated `ArticleAnalysis`, or `None` on any failure (missing key, timeout,
HTTP error, malformed JSON, unexpected shape) - never raises. Every field
from Gemini is defensively clamped/validated against the schema; invalid
enum values fall back to safe defaults rather than propagating garbage.

Analysis includes: `relevant`, `relevance_score` (0-100), `sentiment`,
`impact` + `impact_score` (0-100), `category`, `time_horizon`, `confidence`
(0-1), `summary`, `portfolio_implication`, `reason`, and three fields that
separate fact from speculation:

- `key_facts` - only what the source explicitly states.
- `interpretation` - what the event could plausibly mean, in hedged
  language.
- `uncertainty_notes` - what's not known that would matter for a fuller
  read.

...plus `materiality` (trivial/low/moderate/high/critical) - how big a deal
the event is on its own terms, independent of this holding's portfolio
weight (that weighting happens next, in scoring).

### 2.8 Portfolio impact scoring (`alert_scoring.py`)

```
alert_score = impact_score
              x (portfolio_weight_percent / 100)
              x confidence
              x source_quality_weight   (1.0 / 0.75 / 0.5 for tier 1/2/3)
              x recency_weight          (1.0 same-day, decaying to 0.5 by 7 days)
```

Bounded to 0-100. Explicitly **not** a return prediction - it answers "how
important is this information to this user's specific portfolio",
combining the story's own significance with how much of the user's money is
actually exposed to it.

`notification_tier` is derived from `impact` (not `alert_score`) via
documented thresholds in `constants.py::ImpactLevel.from_score` /
`NotificationTier.from_impact_level`:

| impact_score | ImpactLevel | NotificationTier | Behavior |
|---|---|---|---|
| 81-100 | Critical | Critical | Notify immediately |
| 61-80 | High | High | Notify immediately |
| 41-60 | Moderate | Moderate | Digest only, no immediate push |
| 21-40 | Low | Low | Stored for reference only |
| 0-20 | Very Low | Low | Stored for reference only |

### 2.9 Alert creation (`notification_creation.py`)

`create_alert_from_analysis(user, article, holding, analysis)` is
idempotent via a unique DB constraint on `(user, article, holding_type,
holding_id)` - calling it twice for the same combination returns the
existing row, never a duplicate. This is what makes the whole monitor safe
to re-run.

Two deterministic floors are applied on top of the AI's own judgment (see
`pipeline.py`, both configurable - `NEWS_MONITOR_MIN_RELEVANCE_SCORE` and
`NEWS_MONITOR_MIN_ALERT_SCORE`): if either is below threshold, the alert row
is still created (so the article is never re-sent to Gemini) but marked
`relevant=False`, hiding it from the feed without losing idempotency.

### 2.10 Notification aggregation and digest (`digest.py`)

"Five sources, one notification" is solved structurally at the
deduplication stage (2.5) - there is only ever one `NewsArticle` and one
`PortfolioNewsAlert` per event per user, regardless of how many publishers
covered it; `source_count` on the article is how the UI shows "Reported by
4 sources."

`build_daily_digest(user, for_date=None)` builds the other half: a recap of
a user's CRITICAL/HIGH/MODERATE alerts for a given day (default: today),
ordered by `alert_score`. MODERATE-tier alerts specifically rely on this,
since they're never pushed as an immediate notification. Exposed via `GET
/api/ai/news/digest/?date=YYYY-MM-DD`.

## 3. Data model

### `NewsArticle`
One row per underlying event (not per publisher). Metadata-only - title,
URL, source, publish time, short snippet - never the full article body.
`source_quality` and `source_count` are denormalized from its
`NewsArticleSource` rows so scoring/feed ordering never needs a join.

### `NewsArticleSource`
One row per publisher that reported a given `NewsArticle`. Carries
`publisher_name`, `url`, `quality_tier`, `published_at`. Unique per
`(article, url_hash)`.

### `PortfolioNewsAlert`
One row per `(user, article, holding)` - a specific article's assessed
impact on a specific holding for a specific user, never shared across
users. Carries the AI's full analysis plus scoring outputs
(`alert_score`, `notification_tier`), read state, and notification state.
`relevant=False` rows are kept (hidden from the user-facing feed) purely to
preserve idempotency - the same article is never re-sent to Gemini for that
user/holding.

Both models, plus the migrations that added `NewsArticleSource` and the
newer `PortfolioNewsAlert` fields (`materiality`, `key_facts`,
`interpretation`, `uncertainty_notes`), are backward compatible: existing
`NewsArticle` rows were backfilled with a `NewsArticleSource` snapshot of
their original `source`/`url` rather than losing that history.

## 4. API

All endpoints require authentication and are scoped to `request.user` -
one user's holdings/alerts/preferences are never visible to another.

| Endpoint | Purpose |
|---|---|
| `GET /api/ai/news/` | Feed. Filters: `tier`, `unread_only`, `category`, `sentiment`, `holding_type`, `holding_id`, `date_range` (`today`/`3d`/`7d`/`30d`), `limit`. |
| `GET /api/ai/news/<id>/` | Full detail - AI reasoning, fact/interpretation/uncertainty, all sources. |
| `GET /api/ai/news/digest/?date=YYYY-MM-DD` | Daily digest (defaults to today). |
| `GET /api/ai/notifications/` | Unread CRITICAL/HIGH alerts (notification bell). |
| `POST /api/ai/notifications/<id>/read/` | Mark one alert read. |
| `POST /api/ai/notifications/read-all/` | Mark all read. |
| `POST /api/ai/chat/` | Existing portfolio chatbot - now also answers portfolio-news questions from a bounded summary of the user's recent alerts (see 5.3). |

## 5. Frontend

`frontend/src/features/portfolio-news/` (existing Angular feature,
extended, not replaced) and `frontend/src/core/services/news-api.service.ts`.

- **Feed / Today's Digest toggle** at the top of the news page.
- Feed cards show materiality badge, source count ("Reported by N
  sources"), and the existing tier/sentiment/impact indicators; a secondary
  filter row adds sentiment and date-range filters alongside the existing
  tier filter.
- Detail page adds a "What the source reports / What this could mean /
  What's not yet known" section (fact vs. interpretation vs. uncertainty,
  visually distinguished) and a sources list with clickable links and
  quality-tier badges.
- Digest view renders the numbered, ordered recap matching the product
  spec's example format.

## 6. AI chat integration

The existing single chatbot (`ai/views.py:portfolio_chat`) - not a second
AI system - now also receives a `"news"` section in its context, built by
`ai/services/portfolio_news_context.py:PortfolioNewsChatContextBuilder`.

- Bounded to the last 30 days and 50 alerts (both hardcoded constants in
  that module, since this context rides on every chat call regardless of
  topic - unlike the monitoring env vars, these aren't currently
  environment-configurable) to keep chat token cost predictable.
- The system prompt explicitly tells the model: answer news questions only
  from this supplied data, never invent an event, preserve the
  fact/interpretation/uncertainty distinction, and treat
  tier/materiality/alert_score as importance signals, never return
  predictions.
- If building the news context fails for any reason, chat degrades to "news
  data unavailable" rather than failing the whole request - portfolio chat
  about holdings/transactions must keep working even if news-context
  assembly breaks.

This lets a user ask things like *"What happened to my banking holdings
this week?"* or *"Summarize today's portfolio news"* directly in the
existing chat UI.

## 7. Configuration

Nothing here is hardcoded; every operational threshold reads from an
environment variable with a documented default (see
`services/pipeline.py` and `management/commands/monitor_portfolio_news.py`
for the source of truth).

| Variable | Default | Meaning |
|---|---|---|
| `GEMINI_API_KEY` (or `GOOGLE_API_KEY`) | - | Required for AI analysis and chat. |
| `GEMINI_MODEL` | `gemini-3.6-flash` | Model used for both article analysis and chat. |
| `NEWS_MONITOR_LOOKBACK_DAYS` | `3` | How far back to search for news. |
| `NEWS_MONITOR_AI_CALL_DELAY_SECONDS` | `4.0` | Pacing between Gemini calls (stays under free-tier RPM limits). |
| `NEWS_MONITOR_MAX_ARTICLES_PER_HOLDING` | `15` | Caps AI calls per holding per run; excess candidates are deferred to the next run, never dropped. |
| `NEWS_MONITOR_MIN_RELEVANCE_SCORE` | `30` | Below this, an alert is created (idempotency preserved) but hidden from the feed. |
| `NEWS_MONITOR_MIN_ALERT_SCORE` | `2.0` | Same idea, against the final portfolio-weighted score. |
| `NEWS_MONITOR_INTERVAL` | `1800` (30 min) | Sleep between runs when using `monitor_portfolio_news --loop`. |
| `NEWS_SOURCE_QUALITY_OVERRIDES` (Django setting, not env var) | `{}` | `{substring: tier}` overrides for source-quality classification. |

An invalid (non-numeric) value for any `NEWS_MONITOR_*` variable is treated
as unset and falls back to the default - it never crashes the run.

## 8. Running the monitor

**One-off / cron / Task Scheduler (recommended for production):**

```bash
python manage.py monitor_portfolio_news
```

Schedule this externally every 30-60 minutes.

**Self-looping (e.g. inside a long-lived container with no external
scheduler):**

```bash
NEWS_MONITOR_INTERVAL=1800 python manage.py monitor_portfolio_news --loop
```

Runs immediately, then sleeps `NEWS_MONITOR_INTERVAL` seconds and runs
again, indefinitely. A single run's failure is logged and does not kill the
loop. Stop with Ctrl+C / SIGTERM.

**Programmatic (e.g. from a Celery task or test):**

```python
from portfolio_news.services.pipeline import run_portfolio_news_monitor

stats = run_portfolio_news_monitor(
    lookback_days=7,
    max_articles_per_holding=10,
)
```

Every parameter accepted here overrides the corresponding environment
variable for that call only.

## 9. Testing

```bash
# Whole app
python manage.py test portfolio_news

# A single layer
python manage.py test portfolio_news.tests.QueryBuilderTests
python manage.py test portfolio_news.tests.HoldingMatcherTests
python manage.py test portfolio_news.tests.ArticleStoreDeduplicationTests
python manage.py test portfolio_news.tests.GeminiArticleAnalyzerTests
python manage.py test portfolio_news.tests.AlertScoringTests
python manage.py test portfolio_news.tests.PortfolioNewsPipelineTests
python manage.py test portfolio_news.tests.PortfolioNewsAPITests

# Chat integration (in the ai app)
python manage.py test ai

# Full backend
python manage.py test
```

Coverage includes: RSS parsing and malformed-feed handling; query
generation for equities/funds/sectors/macro; holding matching (exact,
alias, ticker, false-positive rejection, sector/macro fallback and its
rejection cases); dedup (same URL, same headline, cross-source same event);
AI analyzer (valid/malformed/timeout/rate-limit/missing-field responses);
scoring (portfolio-weight, source-quality, and recency effects, each
independently and combined); notification logic (tier mapping, digest
inclusion, immediate-vs-digest); pipeline resilience (one bad article/
holding/query/user doesn't abort the run; reruns are idempotent; the
per-holding article cap defers rather than drops); and cross-user isolation
at both the alert and digest-endpoint level.

## 10. Troubleshooting

**No alerts are being created at all.**
Check `GEMINI_API_KEY`/`GOOGLE_API_KEY` is set - `GeminiArticleAnalyzer`
logs `"no Gemini API key configured, skipping analysis"` and returns `None`
rather than erroring, which can look like silent inactivity. Also check the
command's summary output: `articles_matched` vs `articles_sent_to_ai` vs
`alerts_created` narrows down which stage is filtering everything out.

**A holding's alerts stopped appearing even though news exists.**
Likely `NEWS_MONITOR_MIN_RELEVANCE_SCORE` or `NEWS_MONITOR_MIN_ALERT_SCORE`
- check the `PortfolioNewsAlert` row directly; if it exists with
`relevant=False`, that's the floor being applied, not a pipeline failure.

**Gemini calls are getting rate-limited (HTTP 429).**
Raise `NEWS_MONITOR_AI_CALL_DELAY_SECONDS`, or lower
`NEWS_MONITOR_MAX_ARTICLES_PER_HOLDING` so fewer calls happen per run.

**The same event is showing up as multiple separate feed items.**
Check `NewsArticleSource` for that `NewsArticle` - if publishers are
correctly attached there but you're still seeing duplicates, the issue is
upstream in `ArticleDeduplicator.find_existing` (title similarity threshold
or date window), not in alert creation.

**A macro/sector article isn't matching a holding it should.**
Confirm the holding's `sector` is populated (equities need a linked
`SecurityMaster` with a `sector` value; funds use their `category`) - an
empty sector means `QueryBuilder` never generates sector/macro queries for
that holding at all, by design (see 2.2/2.4).
