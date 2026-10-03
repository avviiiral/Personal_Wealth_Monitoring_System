# Portfolio News Agent — Calculation & Scoring Methodology

This document describes the deterministic, zero-cost calculation pipeline used by the Portfolio News Agent on the `feature/zero-cost-news-agent` branch.

The Portfolio News pipeline does not require a hosted AI/LLM API. News classification, portfolio matching, impact scoring, alert scoring, corporate-filing classification, deduplication, notification tiering, and unusual-activity detection are deterministic.

---

## 1. End-to-End Calculation Flow

The system processes an article/filling through these stages:

1. Build portfolio-aware news queries.
2. Retrieve candidate news/articles.
3. Match the article to portfolio holdings.
4. Calculate holding match strength.
5. Calculate article relevance.
6. Determine sentiment.
7. Determine news category.
8. Calculate article impact score.
9. Convert impact score into an impact level.
10. Determine materiality.
11. Calculate source confidence.
12. Determine time horizon.
13. Calculate portfolio alert score.
14. Apply recency and source-quality weighting.
15. Apply minimum alert/relevance thresholds.
16. Determine notification tier.
17. Deduplicate/cluster related events.
18. Aggregate multiple sources for the same event.
19. Detect unusual activity where sufficient historical data exists.
20. For corporate filings, additionally classify the filing and convert filing severity into the shared scoring model.

The important distinction is:

- **Impact score** answers: “How important is this event in general?”
- **Portfolio alert score** answers: “How important is this event for this specific portfolio holding?”
- **Notification tier** answers: “Should this be surfaced immediately?”
- **Unusual activity** answers: “Is this amount of news activity unusually high compared with the baseline?”

---

# 2. Query Generation

Query generation does not itself produce a numerical score.

For each portfolio holding, the query builder can use:

- Primary company/security name.
- Event-specific terms such as:
  - earnings
  - regulatory
  - acquisition
  - management
  - litigation
  - order
- Security symbol where available.
- Sector-level queries.
- Macro queries.

The generated query set is capped to avoid excessive requests.

The current design can generate up to approximately **15 queries per holding**, including company, event, sector, and macro queries.

These queries are used to retrieve candidate articles from the configured news source, currently including Google News RSS for the general-news path.

---

# 3. Holding Match Score

The holding matcher calculates how strongly an article is connected to a particular portfolio holding.

The match score is based on where and how the identifier appears.

## 3.1 Direct Identifier Matches

| Match condition | Score |
|---|---:|
| Direct company/security identifier in title | 100 |
| Direct company/security identifier in body | 70 |
| Symbol in title | 95 |
| Symbol in body | 65 |
| ISIN in title | 100 |
| ISIN in body | 80 |
| Underlying name in title | 85 |
| Underlying name in body | 60 |
| Underlying ISIN in title | 90 |
| Underlying ISIN in body | 75 |
| Sector/macro relationship | 50 |

A title match is generally stronger because the title is a concentrated representation of the article's subject.

## 3.2 Relevance Threshold

An article is considered relevant to a holding when:

```text
holding_match_score >= 50
```

Therefore:

- 100 = very strong direct match.
- 90–99 = very strong identifier match.
- 70–89 = strong direct/underlying match.
- 50–69 = acceptable contextual/sector relationship.
- Below 50 = not considered relevant to the holding.

The matcher also exposes match strength so the pipeline can prefer stronger holding matches when several portfolio candidates are possible.

---

# 4. Article Relevance Score

The deterministic rule-based analyzer calculates an article-level relevance score.

The current scoring model starts with a base relevance value and increases it when portfolio-specific evidence is found.

The calculation is:

1. Start at **60**.
2. Add **15** for each matched holding term, up to two matched terms.
3. Add **10** when the article comes from a non-broad query.
4. Cap the result at **100**.

Conceptually:

```text
relevance_score =
    60
    + 15 × min(number_of_matched_holding_terms, 2)
    + 10 if query_is_not_broad
```

Then:

```text
relevance_score = min(relevance_score, 100)
```

This score is separate from the holding match score.

### Example

If an article:

- matches two holding terms, and
- came from a specific company/event query,

then:

```text
60 + 15 + 15 + 10 = 100
```

---

# 5. Sentiment Calculation

Sentiment is deterministic and keyword based.

The analyzer counts configured positive and negative signals.

Possible outputs include:

- Positive
- Negative
- Mixed
- Neutral

Conceptually:

```text
positive_count = number of positive keyword matches
negative_count = number of negative keyword matches
```

The resulting sentiment is determined from the relative presence of positive and negative signals.

This is not an LLM sentiment model. It is intentionally deterministic so that the Portfolio News system can operate without an AI API key.

Sentiment is an input to interpretation and does not by itself determine the portfolio alert score.

---

# 6. News Category Calculation

The analyzer maps articles into deterministic categories using keyword/rule groups.

Typical categories include:

- Earnings / financial results
- Regulatory
- Management
- M&A
- Litigation
- Orders/contracts
- Fund raising
- Dividends
- Buybacks
- Corporate actions
- Ratings
- Insolvency/default
- Other

The category is selected from the strongest matching rule group.

Category selection is separate from the numerical impact score.

---

# 7. Impact Score

The impact score estimates how materially important the article is independent of the exact portfolio allocation.

The current deterministic model uses:

- Base score: **20**
- Critical signals: **+38 each**, maximum 2 counted
- High-impact signals: **+22 each**, maximum 3 counted
- Medium-impact signals: **+8 each**, maximum 4 counted
- Source-tier contribution: **+8 / +4 / +0**, depending on source quality
- Final score capped at **100**

Conceptually:

```text
impact_score =
    20
    + critical_signal_points
    + high_signal_points
    + medium_signal_points
    + source_quality_points
```

With the signal-count limits:

```text
critical contribution <= 2 × 38
high contribution     <= 3 × 22
medium contribution   <= 4 × 8
```

Finally:

```text
impact_score = min(impact_score, 100)
```

## 7.1 Avoiding Double Counting

The analyzer contains logic to avoid unnecessarily double-counting generic medium-impact signals when a more specific high-impact signal has already captured the same event.

For example, a highly specific severe event should not receive an additional unrelated generic increment merely because the same wording also happens to match a broad medium-impact keyword.

---

# 8. Impact Level

The numerical impact score is converted into a categorical impact level.

| Impact score | Impact level |
|---:|---|
| 81–100 | CRITICAL |
| 61–80 | HIGH |
| 41–60 | MODERATE |
| 21–40 | LOW |
| 0–20 | VERY LOW |

This mapping is deterministic.

### Example

An impact score of:

- 95 → CRITICAL
- 72 → HIGH
- 50 → MODERATE
- 30 → LOW
- 15 → VERY LOW

---

# 9. Materiality

Materiality uses the same numerical bands as impact level.

| Score | Materiality |
|---:|---|
| 81–100 | CRITICAL |
| 61–80 | HIGH |
| 41–60 | MODERATE |
| 21–40 | LOW |
| 0–20 | TRIVIAL |

Materiality answers a slightly different question from sentiment:

> How materially important is this event to the security/business?

A negative article is not automatically highly material, and a positive article is not automatically highly material.

---

# 10. Confidence Calculation

Confidence represents how much trust the deterministic analyzer has in its interpretation.

The current model starts at:

```text
confidence = 0.78
```

Penalties can apply when important evidence is missing.

Current deductions include:

| Condition | Adjustment |
|---|---:|
| Missing article description | -0.12 |
| Missing source / Google News-type source | -0.08 |
| Broad query without direct holding match | -0.08 |

The final confidence is bounded:

```text
confidence >= 0.35
confidence <= 0.95
```

Therefore:

```text
confidence = clamp(calculated_confidence, 0.35, 0.95)
```

Confidence is later used directly in portfolio alert scoring.

---

# 11. Time Horizon

The analyzer assigns a deterministic time horizon based on the event type/signals.

The purpose is to distinguish between events that are:

- Immediate
- Short term
- Medium term
- Long term
- Unclear / not determinable

Examples of interpretation include:

- Regulatory or major corporate events → potentially immediate/short-term.
- Earnings-related events → short/medium-term relevance.
- Strategic acquisitions or structural changes → potentially medium/long-term.

Time horizon is descriptive and does not replace the numerical alert score.

---

# 12. Portfolio Alert Score

This is the most important portfolio-specific calculation.

The alert score combines:

1. Article impact.
2. Portfolio exposure/holding weight.
3. Analyzer confidence.
4. Source quality.
5. Recency.

The formula is:

```text
alert_score =
    impact_score
    × (portfolio_weight_percent / 100)
    × confidence
    × source_quality_weight
    × recency_weight
```

The final value is clamped to:

```text
0 <= alert_score <= 100
```

and rounded to two decimal places.

## 12.1 Why Portfolio Weight Matters

A major event affecting a company is not equally important to every portfolio.

For example:

- Holding A has 2% portfolio weight.
- Holding B has 25% portfolio weight.

If the same event has the same impact and confidence, Holding B should normally produce a materially larger portfolio alert score.

The portfolio weight therefore converts generic news importance into portfolio-specific importance.

---

# 13. Source Quality Weight

The alert score applies a source-quality multiplier.

Conceptually:

```text
alert_score ∝ source_quality_weight
```

Higher-quality sources receive a stronger contribution than weaker/broader sources.

This prevents a low-quality or weakly attributed article from automatically receiving the same portfolio importance as a high-quality source.

The source-quality system is intentionally deterministic and does not require an external AI service.

---

# 14. Recency Weight

News becomes less urgent as it ages.

The current recency calculation is:

### Article <= 1 day old

```text
recency_weight = 1.0
```

### Article between 1 and 7 days old

The weight decreases linearly from 1.0 to 0.5:

```text
recency_weight =
    1.0 - ((age_days - 1) / 6) × 0.5
```

### Article >= 7 days old

```text
recency_weight = 0.5
```

### Missing publication timestamp

```text
recency_weight = 1.0
```

This prevents older articles from being treated as equally urgent as new developments while still allowing older material to remain useful.

---

# 15. Complete Alert Score Example

Assume:

- Impact score = 80
- Portfolio weight = 12%
- Confidence = 0.90
- Source quality weight = 1.00
- Recency weight = 0.90

Then:

```text
alert_score =
    80
    × 0.12
    × 0.90
    × 1.00
    × 0.90

= 7.776
```

Rounded:

```text
alert_score = 7.78
```

The important point is that an article can have a high generic impact score while still producing a relatively modest portfolio alert if the affected holding represents a small portion of the portfolio.

---

# 16. Minimum Relevance Threshold

The Portfolio News monitor uses:

```text
NEWS_MONITOR_MIN_RELEVANCE_SCORE = 30
```

Articles below the configured relevance threshold are not treated as meaningful portfolio news.

The pipeline also contains a consistency check that can mark an alert as non-relevant when its final alert score falls below the configured minimum while it was otherwise marked relevant.

---

# 17. Minimum Alert Score

The default minimum alert score is:

```text
NEWS_MONITOR_MIN_ALERT_SCORE = 2.0
```

An alert with a score below this threshold is not considered sufficiently important for the normal portfolio alert path.

This prevents very small portfolio exposures combined with weak/old/low-confidence news from flooding the Portfolio News feed.

---

# 18. Notification Tier

Notification priority is determined primarily from impact severity.

The system distinguishes between:

- Informational news
- Normal portfolio news
- Higher-priority alerts
- Critical alerts

Immediate Web Push notifications are restricted to **HIGH** and **CRITICAL** alerts, subject to the notification/cooldown rules.

This separation is intentional:

> Not every article that is stored should generate a push notification.

The Portfolio News page can contain more information than the immediate notification channel.

---

# 19. Notification Cooldown

To prevent repeated notifications for the same event, the system uses:

```text
NEWS_NOTIFICATION_COOLDOWN = 86400 seconds
```

which is:

```text
24 hours
```

The cooldown is particularly important when multiple publishers report the same event or when a corporate filing and a news article refer to the same underlying event.

---

# 20. Event Deduplication and Clustering

Deduplication is not a financial score. It determines whether multiple articles represent the same event.

The system uses several levels of matching:

1. Exact URL match.
2. Exact article fingerprint.
3. Fuzzy title similarity.
4. Conservative event-family/entity-token matching.

The fuzzy title threshold is approximately:

```text
0.72
```

The event-cluster window is configurable and currently defaults to:

```text
NEWS_EVENT_CLUSTER_WINDOW = 3
```

The purpose is to avoid showing:

- Reuters report
- Exchange filing
- Economic Times report
- Company announcement

as four completely unrelated alerts when they all describe the same event.

---

# 21. Cross-Source Aggregation

When multiple sources describe the same event, the system can aggregate the sources into the same event cluster.

This provides two benefits:

1. Reduced duplicate alerts.
2. Increased confidence through independent source coverage.

Corporate filings can therefore upgrade or enrich an existing news event instead of necessarily creating a second unrelated alert.

---

# 22. Holding Match Priority

When an article can potentially match several holdings, the pipeline sorts candidates by match strength.

The strongest identifiers are preferred:

1. ISIN/direct security identifier.
2. Company/security name.
3. Symbol.
4. Underlying identifiers.
5. Body-only evidence.
6. Sector/macro relationship.

This reduces accidental association of a company-specific article with an unrelated holding merely because both belong to the same sector.

---

# 23. Corporate Filing Intelligence

Corporate filings use a separate deterministic classifier before entering the shared Portfolio News scoring pipeline.

The classifier assigns a base event score according to the filing type.

## 23.1 Filing Base Scores

| Filing event | Base score |
|---|---:|
| FRAUD | 92 |
| FRAUD_ALLEGATION | 72 |
| INSOLVENCY | 92 |
| BANKRUPTCY | 95 |
| DEFAULT | 90 |
| REGULATORY_ACTION | 68 |
| RATING_DOWNGRADE | 68 |
| REGULATORY_BAN | 90 |
| MAJOR_REGULATORY_ACTION | 82 |
| AUDITOR_QUALIFICATION | 84 |
| CEO_RESIGNATION | 70 |
| CFO_RESIGNATION | 70 |
| DIRECTOR_RESIGNATION | 65 |
| AUDITOR_RESIGNATION | 68 |
| MAJOR_INVESTIGATION | 68 |
| MAJOR_ACQUISITION | 66 |
| ACQUISITION | 50 |
| MERGER | 58 |
| DEMERGER | 58 |
| MAJOR_FUND_RAISING | 66 |
| FUND_RAISING | 48 |
| PREFERENTIAL_ISSUE | 50 |
| BUYBACK | 48 |
| BONUS | 45 |
| STOCK_SPLIT | 45 |
| DIVIDEND | 45 |
| SIGNIFICANT_PROMOTER_PLEDGE | 66 |
| PROMOTER_PLEDGE_RELEASE | 48 |
| PROMOTER_PLEDGE | 50 |
| CHANGE_IN_SHAREHOLDING | 48 |
| PROMOTER_TRANSACTION | 50 |
| MATERIAL_ORDER | 66 |
| ORDER | 48 |
| MATERIAL_CONTRACT | 65 |
| CONTRACT | 48 |
| MATERIAL_LITIGATION | 55 |
| LITIGATION | 45 |
| FINANCIAL_RESULTS | 48 |
| BOARD_MEETING | 45 |
| INVESTOR_PRESENTATION | 35 |
| EARNINGS_CALL | 35 |
| ROUTINE_CORPORATE_ANNOUNCEMENT | 22 |

---

# 24. Filing Score Bonuses

Two additional contextual bonuses can apply.

## Subject bonus

If the relevant event pattern is found in the filing subject:

```text
+8
```

## Filing type bonus

If relevant event words are also found in the filing type:

```text
+4
```

Therefore:

```text
filing_score =
    base_rule_score
    + subject_bonus
    + filing_type_bonus
    + adverse_combination_bonuses
```

The final filing score is capped at 100.

---

# 25. Combined Adverse Filing Signals

The filing classifier also detects combinations that are more significant together than individually.

## Profit pressure + guidance cut

```text
+12
```

## Investigation + management exit

```text
+12
```

## Multiple adverse rule hits

When at least two adverse filing rules are triggered:

```text
+10
```

Final filing score:

```text
filing_score = min(calculated_score, 100)
```

This allows the classifier to recognize compound corporate events instead of treating each keyword in isolation.

---

# 26. Filing Severity

The filing score is converted to a filing severity.

| Filing score | Severity |
|---:|---|
| >= 81 | CRITICAL |
| 61–80 | HIGH |
| 41–60 | MEDIUM |
| 21–40 | LOW |
| < 21 | INFO |

---

# 27. Filing Severity → Shared Portfolio Score

Corporate filings are converted into the shared Portfolio News score model using:

| Filing severity | Shared score |
|---|---:|
| INFO | 20 |
| LOW | 30 |
| MEDIUM | 50 |
| HIGH | 70 |
| CRITICAL | 90 |

This allows normal news and corporate filings to enter the same Portfolio News architecture.

The filing then goes through:

- portfolio matching,
- event clustering,
- confidence/source aggregation,
- portfolio alert scoring,
- notification tiering.

---

# 28. Filing Materiality

Corporate filing materiality is mapped from the filing severity/score into:

- Critical
- High
- Moderate
- Low
- Trivial

The purpose is to expose the material significance of the corporate event in the same language used by the general Portfolio News system.

---

# 29. Corporate Filing Matching

A filing can be matched to portfolio assets using identifiers such as:

- ISIN
- Security symbol
- BSE code
- Company name

The filing can then be associated with:

- portfolio holdings,
- users holding the security,
- watchlist entries where supported.

This makes a corporate announcement portfolio-aware rather than simply displaying a global exchange feed.

---

# 30. Market Session Context

Corporate filing processing also determines the market session context:

- Pre-market
- Market-hours
- Post-market
- Weekend

This context is included in the filing explanation/reasoning.

It provides useful timing information without requiring an AI model.

---

# 31. Unusual Activity Calculation

The news agent can identify unusually high news activity for a holding.

The current deterministic conditions are:

### Recent activity requirement

At least:

```text
5 recent matches
```

### Baseline comparison

Recent activity must exceed:

```text
2.5 × baseline daily average
```

Conceptually:

```text
unusual_activity =
    recent_match_count >= 5
    AND recent_activity > 2.5 × baseline_daily_average
```

This is informational rather than an independent financial recommendation.

It is intended to answer:

> “Is there substantially more news activity around this holding than is normal?”

---

# 32. Why the System Uses Multiple Scores

A single score would create several problems.

For example:

- A highly negative article about a company with a 0.5% portfolio allocation may not deserve an urgent push.
- A moderately important event involving a 30% portfolio allocation may deserve immediate attention.
- A fresh exchange filing may be more actionable than a week-old commentary article.
- Multiple reputable sources covering the same event provide stronger evidence than one weak source.
- A broad sector article should not be treated as equivalent to a direct company announcement.

Therefore the system deliberately separates:

```text
Match
Relevance
Sentiment
Category
Impact
Materiality
Confidence
Recency
Portfolio exposure
Source quality
Alert score
Notification tier
Unusual activity
```

---

# 33. Complete Mathematical Chain

For general news, the conceptual pipeline is:

```text
Article
  ↓
Holding Match Score
  ↓
Relevance Score
  ↓
Sentiment + Category
  ↓
Impact Score
  ↓
Impact Level + Materiality
  ↓
Confidence
  ↓
Portfolio Weight
  ↓
Source Quality Weight
  ↓
Recency Weight
  ↓
Portfolio Alert Score
  ↓
Minimum Threshold
  ↓
Notification Tier
```

The central portfolio calculation is:

```text
Portfolio Alert Score =
    Impact Score
    × Portfolio Weight
    × Confidence
    × Source Quality Weight
    × Recency Weight
```

where:

```text
Portfolio Weight = portfolio_weight_percent / 100
```

and the final result is clamped to 0–100.

---

# 34. Worked Portfolio Example

Assume a company has:

- Impact score = 90
- Portfolio allocation = 20%
- Confidence = 0.90
- Source quality weight = 1.00
- Recency weight = 1.00

Then:

```text
Alert Score =
    90 × 0.20 × 0.90 × 1.00 × 1.00

= 16.20
```

Now assume the same event affects a company with only a 2% allocation:

```text
Alert Score =
    90 × 0.02 × 0.90 × 1.00 × 1.00

= 1.62
```

The same event therefore has very different portfolio importance.

Because the default minimum alert score is 2.0:

- 16.20 → qualifies.
- 1.62 → falls below the default alert threshold.

This demonstrates why portfolio allocation is an essential part of the scoring model.

---

# 35. Important Design Principles

## Deterministic

The same input should produce the same classification and score.

## Zero-cost

The Portfolio News intelligence path does not require a paid AI/LLM API.

## Portfolio-aware

News importance is adjusted according to actual portfolio exposure.

## Source-aware

Source quality affects confidence/alert scoring.

## Recency-aware

Newer events receive stronger urgency.

## Event-aware

Multiple reports about the same event are clustered.

## Filing-aware

Exchange/company filings are classified separately and then integrated into the same shared Portfolio News model.

## Notification-aware

Storage and immediate notification are intentionally separated.

## Conservative

Weak or broad signals should not automatically create high-priority portfolio alerts.

---

# 36. Configuration Values Relevant to Scoring

Current/default configuration includes:

```text
NEWS_MONITOR_MIN_RELEVANCE_SCORE = 30
NEWS_MONITOR_MIN_ALERT_SCORE = 2.0
NEWS_EVENT_CLUSTER_WINDOW = 3
NEWS_NOTIFICATION_COOLDOWN = 86400
```

Corporate filing ingestion is feature controlled:

```text
NEWS_CORPORATE_FILINGS_ENABLED = False
NEWS_NSE_FILINGS_ENABLED = True
NEWS_BSE_FILINGS_ENABLED = True
```

Corporate filing ingestion therefore remains disabled unless the required feature/feed configuration is explicitly enabled.

---

# 37. What Each Number Means

| Metric | Meaning |
|---|---|
| Holding Match Score | How strongly the article belongs to a holding |
| Relevance Score | How relevant the article is to the portfolio/news query |
| Sentiment | Direction of the article's tone |
| Impact Score | Generic significance of the event |
| Impact Level | Human-readable severity of impact |
| Materiality | How materially important the event is |
| Confidence | Confidence in the deterministic interpretation |
| Portfolio Weight | Portion of the portfolio exposed to the holding |
| Source Quality | Reliability contribution of the source |
| Recency Weight | Urgency contribution from article age |
| Alert Score | Final portfolio-specific importance |
| Notification Tier | Whether/how aggressively to notify |
| Unusual Activity | Whether news volume is abnormally high |

---

# 38. Key Distinction: Impact vs Alert Score

This is the most important conceptual distinction in the system.

### Impact Score

```text
“How important is this event?”
```

### Alert Score

```text
“How important is this event for this portfolio?”
```

Therefore:

```text
High Impact ≠ automatically High Portfolio Alert
```

A high-impact event affecting a tiny portfolio position may have a modest alert score.

Conversely:

```text
Moderate Impact × Large Portfolio Weight
```

can produce a meaningful portfolio alert.

---

# 39. Current Limitations

The scoring model is intentionally deterministic and therefore has limitations.

It does not perform:

- semantic LLM interpretation,
- probabilistic financial reasoning,
- analyst-level fundamental valuation,
- automatic causal inference,
- full article understanding when the provider only exposes a headline/snippet,
- prediction of future stock returns.

For Google News RSS, the system generally works from the information made available by the feed, such as headline, publisher and description/snippet. Full article content is not guaranteed.

Corporate filing intelligence similarly depends on the configured authorized filing/feed source.

The system should therefore be interpreted as a **portfolio news prioritization engine**, not an autonomous investment decision-maker.

---

# 40. Summary

The Portfolio News Agent uses a layered deterministic calculation model:

```text
Portfolio-aware query
        ↓
Holding Match
        ↓
Relevance
        ↓
Sentiment / Category
        ↓
Impact
        ↓
Materiality / Severity
        ↓
Confidence
        ↓
Portfolio Exposure
        ↓
Source Quality
        ↓
Recency
        ↓
Portfolio Alert Score
        ↓
Threshold
        ↓
Notification
```

Corporate filings add a dedicated deterministic filing-classification layer before entering this shared pipeline.

The central objective is to prioritize **the news that matters most to the actual portfolio**, rather than simply ranking news by how dramatic a headline looks.

