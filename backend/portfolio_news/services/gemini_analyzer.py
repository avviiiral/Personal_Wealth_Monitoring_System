import json
import logging

from dataclasses import dataclass

from typing import Optional

import requests

from ai.views import (
    GEMINI_API_BASE,
    extract_response_text,
    get_gemini_api_key,
    get_gemini_model,
)

from ..constants import (
    ImpactLevel,
    Materiality,
    NewsCategory,
    Sentiment,
    TimeHorizon,
)


logger = logging.getLogger(__name__)


REQUEST_TIMEOUT_SECONDS = 60


SYSTEM_INSTRUCTIONS = """
You are the news-analysis component of the Personal Wealth
Monitoring System (PWMS) Portfolio News Intelligence agent.

You will be given a BATCH of news articles. Each article is
explicitly associated with one holding from the user's real
PWMS portfolio.

Analyze EVERY article in the batch independently.

IMPORTANT RULES:

1. Return exactly ONE analysis result for EVERY supplied article.

2. Each result MUST preserve the supplied article_id and
   holding_type/holding_id so the application can map the
   analysis back to the correct article and holding.

3. Base your assessment ONLY on the supplied article headline,
   description/snippet, source, publish time, and supplied
   holding information. Never invent facts, figures, or events.

4. If the supplied article information does not contain enough
   information for a confident assessment, lower confidence,
   relevance_score, and impact_score rather than guessing.

5. This is NOT investment advice. Never tell the user to buy,
   sell, hold, or take any specific action. Never guarantee or
   predict returns.

6. relevance_score and impact_score are integers from 0 to 100.

7. confidence is a float from 0.0 to 1.0.

8. impact reflects the potential magnitude of the news for the
   SPECIFIC supplied holding, not the market in general.

9. summary must be a short, factual, 1-2 sentence summary of
   what the article actually says.

10. portfolio_implication and reason must clearly be framed as
    an AI assessment and must use hedged language.

11. materiality is your judgment of how significant the reported
    event is in its own right for the company/sector:
    trivial, low, moderate, high, or critical.

12. key_facts must contain only facts explicitly stated in the
    article.

13. interpretation must clearly distinguish inference from facts
    and use hedged language such as "could", "may", or
    "this may suggest".

14. uncertainty_notes must identify meaningful information that
    is not available in the supplied article.

15. Analyze each article independently. Do not allow information
    from one article to influence the analysis of another article.

16. Return ONLY the JSON array described by the response schema.
    No prose and no markdown.

17. Do not omit any supplied article.

18. The number of returned results MUST equal the number of
    supplied articles.
"""


ARTICLE_ANALYSIS_RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "relevant": {"type": "BOOLEAN"},
        "relevance_score": {"type": "INTEGER"},
        "sentiment": {
            "type": "STRING",
            "enum": [choice.value for choice in Sentiment],
        },
        "impact": {
            "type": "STRING",
            "enum": [choice.value for choice in ImpactLevel],
        },
        "impact_score": {"type": "INTEGER"},
        "category": {
            "type": "STRING",
            "enum": [choice.value for choice in NewsCategory],
        },
        "time_horizon": {
            "type": "STRING",
            "enum": [choice.value for choice in TimeHorizon],
        },
        "summary": {"type": "STRING"},
        "portfolio_implication": {"type": "STRING"},
        "reason": {"type": "STRING"},
        "confidence": {"type": "NUMBER"},
        "materiality": {
            "type": "STRING",
            "enum": [choice.value for choice in Materiality],
        },
        "key_facts": {"type": "STRING"},
        "interpretation": {"type": "STRING"},
        "uncertainty_notes": {"type": "STRING"},
    },
    "required": [
        "relevant",
        "relevance_score",
        "sentiment",
        "impact",
        "impact_score",
        "category",
        "time_horizon",
        "summary",
        "portfolio_implication",
        "reason",
        "confidence",
        "materiality",
        "key_facts",
        "interpretation",
        "uncertainty_notes",
    ],
}


BATCH_ARTICLE_ANALYSIS_RESPONSE_SCHEMA = {
    "type": "ARRAY",
    "items": {
        "type": "OBJECT",
        "properties": {
            "article_id": {"type": "INTEGER"},
            "holding_type": {"type": "STRING"},
            "holding_id": {"type": "INTEGER"},
            "relevant": {"type": "BOOLEAN"},
            "relevance_score": {"type": "INTEGER"},
            "sentiment": {
                "type": "STRING",
                "enum": [choice.value for choice in Sentiment],
            },
            "impact": {
                "type": "STRING",
                "enum": [choice.value for choice in ImpactLevel],
            },
            "impact_score": {"type": "INTEGER"},
            "category": {
                "type": "STRING",
                "enum": [choice.value for choice in NewsCategory],
            },
            "time_horizon": {
                "type": "STRING",
                "enum": [choice.value for choice in TimeHorizon],
            },
            "summary": {"type": "STRING"},
            "portfolio_implication": {"type": "STRING"},
            "reason": {"type": "STRING"},
            "confidence": {"type": "NUMBER"},
            "materiality": {
                "type": "STRING",
                "enum": [choice.value for choice in Materiality],
            },
            "key_facts": {"type": "STRING"},
            "interpretation": {"type": "STRING"},
            "uncertainty_notes": {"type": "STRING"},
        },
        "required": [
            "article_id",
            "holding_type",
            "holding_id",
            "relevant",
            "relevance_score",
            "sentiment",
            "impact",
            "impact_score",
            "category",
            "time_horizon",
            "summary",
            "portfolio_implication",
            "reason",
            "confidence",
            "materiality",
            "key_facts",
            "interpretation",
            "uncertainty_notes",
        ],
    },
}


def _clamp_int(value, low, high, default):
    try:
        return max(low, min(high, int(value)))
    except (TypeError, ValueError):
        return default


def _clamp_float(value, low, high, default):
    try:
        return max(low, min(high, float(value)))
    except (TypeError, ValueError):
        return default


def _validate_choice(value, valid_values, default):
    if isinstance(value, str) and value in valid_values:
        return value

    return default


@dataclass
class ArticleAnalysis:
    """
    Validated result of analyzing one article against one
    holding. Every field is defensively clamped/validated
    against what Gemini returned - the AI's output is never
    trusted blindly, per PWMS's rule that the AI interprets
    but does not author authoritative values.
    """

    relevant: bool
    relevance_score: int
    sentiment: str
    impact: str
    impact_score: int
    category: str
    time_horizon: str
    summary: str
    portfolio_implication: str
    reason: str
    confidence: float
    materiality: str = Materiality.MODERATE
    key_facts: str = ""
    interpretation: str = ""
    uncertainty_notes: str = ""

    @classmethod
    def from_gemini_json(cls, data: dict) -> "ArticleAnalysis":
        impact_score = _clamp_int(
            data.get("impact_score"), 0, 100, 0
        )

        valid_sentiments = {choice.value for choice in Sentiment}
        valid_impacts = {choice.value for choice in ImpactLevel}
        valid_categories = {choice.value for choice in NewsCategory}
        valid_horizons = {choice.value for choice in TimeHorizon}
        valid_materialities = {choice.value for choice in Materiality}

        impact = _validate_choice(
            data.get("impact"),
            valid_impacts,
            ImpactLevel.from_score(impact_score),
        )

        return cls(
            relevant=bool(data.get("relevant", False)),
            relevance_score=_clamp_int(
                data.get("relevance_score"), 0, 100, 0
            ),
            sentiment=_validate_choice(
                data.get("sentiment"),
                valid_sentiments,
                Sentiment.NEUTRAL,
            ),
            impact=impact,
            impact_score=impact_score,
            category=_validate_choice(
                data.get("category"),
                valid_categories,
                NewsCategory.OTHER,
            ),
            time_horizon=_validate_choice(
                data.get("time_horizon"),
                valid_horizons,
                TimeHorizon.UNSPECIFIED,
            ),
            summary=(
                str(data.get("summary", "")).strip()
                or "The AI did not provide a summary for this article."
            ),
            portfolio_implication=(
                str(data.get("portfolio_implication", "")).strip()
                or (
                    "The AI could not determine a specific "
                    "portfolio implication from this article."
                )
            ),
            reason=(
                str(data.get("reason", "")).strip()
                or "No reason was provided by the AI."
            ),
            confidence=_clamp_float(
                data.get("confidence"), 0.0, 1.0, 0.0
            ),
            materiality=_validate_choice(
                data.get("materiality"),
                valid_materialities,
                {
                    ImpactLevel.CRITICAL: Materiality.CRITICAL,
                    ImpactLevel.HIGH: Materiality.HIGH,
                    ImpactLevel.MODERATE: Materiality.MODERATE,
                    ImpactLevel.LOW: Materiality.LOW,
                    ImpactLevel.VERY_LOW: Materiality.TRIVIAL,
                }.get(impact, Materiality.MODERATE),
            ),
            key_facts=str(data.get("key_facts", "")).strip(),
            interpretation=str(data.get("interpretation", "")).strip(),
            uncertainty_notes=str(
                data.get("uncertainty_notes", "")
            ).strip(),
        )


class GeminiArticleAnalyzer:
    """
    Analyzes portfolio news with Gemini.

    The legacy analyze() method remains available for compatibility,
    while analyze_batch() sends multiple article/holding pairs in a
    single Gemini request to reduce request-per-minute pressure.
    """

    def _build_payload(self, article, holding):
        holding_context = {
            "holding_type": holding.holding_type,
            "display_name": holding.display_name,
            "symbol": holding.symbol or None,
            "amc_name": holding.amc_name or None,
            "portfolio_weight_percent": round(
                holding.portfolio_weight, 2
            ),
        }

        article_context = {
            "title": article.title,
            "description": article.description,
            "source": article.source,
            "published_at": (
                article.published_at.isoformat()
                if article.published_at
                else None
            ),
        }

        user_content = (
            "HOLDING:\n"
            f"{holding_context}\n\n"
            "ARTICLE:\n"
            f"{article_context}"
        )

        return {
            "system_instruction": {
                "parts": [{"text": SYSTEM_INSTRUCTIONS}]
            },
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": user_content}],
                }
            ],
            "generationConfig": {
                "responseMimeType": "application/json",
                "responseSchema": ARTICLE_ANALYSIS_RESPONSE_SCHEMA,
            },
        }

    def _record_usage(self, user, model, usage, endpoint):
        from ai.services.usage_tracking import record_gemini_usage

        try:
            record_gemini_usage(
                user=user,
                endpoint=endpoint,
                model_name=model,
                usage_metadata=usage,
            )
        except Exception:
            logger.exception(
                "record_gemini_usage raised unexpectedly for %s; "
                "continuing without it.",
                endpoint,
            )

    def analyze(
        self,
        article,
        holding,
        user=None,
    ) -> Optional[ArticleAnalysis]:
        api_key = get_gemini_api_key()

        if not api_key:
            logger.warning(
                "GeminiArticleAnalyzer: no Gemini API key configured, "
                "skipping analysis."
            )
            return None

        payload = self._build_payload(article, holding)
        model = get_gemini_model()
        gemini_url = f"{GEMINI_API_BASE}/{model}:generateContent"

        try:
            response = requests.post(
                gemini_url,
                headers={
                    "x-goog-api-key": api_key,
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=REQUEST_TIMEOUT_SECONDS,
            )

            response.raise_for_status()
            data = response.json()
            usage = data.get("usageMetadata", {})

            logger.info(
                "Gemini usage | article=%s | holding=%r | input=%s | "
                "output=%s | total=%s | cached=%s",
                getattr(article, "id", None),
                holding.display_name,
                usage.get("promptTokenCount", 0),
                usage.get("candidatesTokenCount", 0),
                usage.get("totalTokenCount", 0),
                usage.get("cachedContentTokenCount", 0),
            )

            self._record_usage(
                user=user,
                model=model,
                usage=usage,
                endpoint="article_analysis",
            )

            raw_text = extract_response_text(data)

            if not raw_text:
                logger.warning(
                    "GeminiArticleAnalyzer: empty response for article "
                    "id=%s holding=%r",
                    getattr(article, "id", None),
                    holding.display_name,
                )
                return None

            parsed = json.loads(raw_text)
            return ArticleAnalysis.from_gemini_json(parsed)

        except requests.exceptions.RequestException as exc:
            logger.warning(
                "GeminiArticleAnalyzer: request failed for article "
                "id=%s holding=%r: %s",
                getattr(article, "id", None),
                holding.display_name,
                exc,
            )
            return None

        except (ValueError, KeyError, TypeError) as exc:
            logger.warning(
                "GeminiArticleAnalyzer: could not parse response for "
                "article id=%s holding=%r: %s",
                getattr(article, "id", None),
                holding.display_name,
                exc,
            )
            return None

    def analyze_batch(
        self,
        article_holding_pairs,
        user=None,
    ) -> dict:
        """
        Analyze multiple (article, holding) pairs with one Gemini request.

        Returns a mapping keyed by:
            (article_id, holding_type, holding_id)
        to ArticleAnalysis.
        """
        if not article_holding_pairs:
            return {}

        api_key = get_gemini_api_key()

        if not api_key:
            logger.warning(
                "GeminiArticleAnalyzer: no Gemini API key configured, "
                "skipping batch analysis."
            )
            return {}

        batch_items = []

        for article, holding in article_holding_pairs:
            batch_items.append(
                {
                    "article_id": article.id,
                    "holding": {
                        "holding_type": holding.holding_type,
                        "holding_id": holding.holding_id,
                        "display_name": holding.display_name,
                        "symbol": holding.symbol or None,
                        "amc_name": holding.amc_name or None,
                        "portfolio_weight_percent": round(
                            holding.portfolio_weight, 2
                        ),
                    },
                    "article": {
                        "title": article.title,
                        "description": article.description,
                        "source": article.source,
                        "published_at": (
                            article.published_at.isoformat()
                            if article.published_at
                            else None
                        ),
                    },
                }
            )

        user_content = (
            "Analyze every article/holding pair below independently. "
            "Return exactly one result for every article_id.\n\n"
            + json.dumps(batch_items, ensure_ascii=False)
        )

        payload = {
            "system_instruction": {
                "parts": [{"text": SYSTEM_INSTRUCTIONS}]
            },
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": user_content}],
                }
            ],
            "generationConfig": {
                "responseMimeType": "application/json",
                "responseSchema": BATCH_ARTICLE_ANALYSIS_RESPONSE_SCHEMA,
            },
        }

        model = get_gemini_model()
        gemini_url = f"{GEMINI_API_BASE}/{model}:generateContent"

        try:
            response = requests.post(
                gemini_url,
                headers={
                    "x-goog-api-key": api_key,
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=REQUEST_TIMEOUT_SECONDS,
            )

            response.raise_for_status()
            data = response.json()
            usage = data.get("usageMetadata", {})

            logger.info(
                "Gemini batch usage | user=%s | articles=%s | input=%s | "
                "output=%s | total=%s | cached=%s",
                getattr(user, "id", None),
                len(article_holding_pairs),
                usage.get("promptTokenCount", 0),
                usage.get("candidatesTokenCount", 0),
                usage.get("totalTokenCount", 0),
                usage.get("cachedContentTokenCount", 0),
            )

            self._record_usage(
                user=user,
                model=model,
                usage=usage,
                endpoint="article_analysis_batch",
            )

            raw_text = extract_response_text(data)

            if not raw_text:
                logger.warning(
                    "GeminiArticleAnalyzer: empty batch response for user=%s",
                    getattr(user, "id", None),
                )
                return {}

            parsed = json.loads(raw_text)

            if not isinstance(parsed, list):
                logger.warning(
                    "GeminiArticleAnalyzer: expected batch JSON array but "
                    "received %s",
                    type(parsed).__name__,
                )
                return {}

            results = {}

            for item in parsed:
                if not isinstance(item, dict):
                    continue

                try:
                    article_id = int(item["article_id"])
                    holding_type = str(item["holding_type"])
                    holding_id = int(item["holding_id"])
                except (KeyError, TypeError, ValueError):
                    logger.warning(
                        "GeminiArticleAnalyzer: invalid batch identity record: %r",
                        item,
                    )
                    continue

                results[
                    (article_id, holding_type, holding_id)
                ] = ArticleAnalysis.from_gemini_json(item)

            logger.info(
                "Gemini batch completed | user=%s | requested=%s | returned=%s",
                getattr(user, "id", None),
                len(article_holding_pairs),
                len(results),
            )

            return results

        except requests.exceptions.RequestException as exc:
            logger.warning(
                "GeminiArticleAnalyzer: batch request failed for user=%s: %s",
                getattr(user, "id", None),
                exc,
            )
            return {}

        except (ValueError, KeyError, TypeError) as exc:
            logger.warning(
                "GeminiArticleAnalyzer: could not parse batch response for "
                "user=%s: %s",
                getattr(user, "id", None),
                exc,
            )
            return {}
