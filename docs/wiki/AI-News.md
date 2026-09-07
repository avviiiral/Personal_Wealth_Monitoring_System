# AI Chat & Portfolio News

## AI Portfolio Chat

PWMS provides a Gemini-backed portfolio assistant. The backend builds structured portfolio context from the user's data and sends that context to Gemini for interpretation.

The AI layer is not the authoritative calculation engine: portfolio numbers are computed by the application and supplied to the assistant as context. Questions about holdings, allocation, performance and related metrics should therefore trace back to the underlying portfolio/analytics services.

Gemini usage can be tracked through the AI usage logging model so input/output usage can be reviewed rather than estimated only from prompts.

## Portfolio News Intelligence

The portfolio news feature is designed around the user's actual holdings rather than a hard-coded stock list.

```text
User holdings
     |
     v
News discovery
     |
     v
Deterministic article/holding matching
     |
     v
AI analysis only for relevant matches
     |
     v
Impact scoring
     |
     v
Portfolio news alert
```

The news layer retrieves articles from Google News RSS, deduplicates/matches them against holdings and uses AI only after deterministic relevance checks. Alerts are scored using portfolio impact factors and can generate browser notifications for high-impact items.

## AI safety principle

AI output should explain or summarize portfolio information that has already been computed by PWMS. It should not be treated as a replacement for transaction calculations, authorization checks or source-data validation.

## Operational considerations

Gemini usage is subject to the configured API plan/quota and can return rate-limit responses. News-agent calls should therefore respect the configured request delay and avoid unnecessary AI calls for irrelevant articles.
