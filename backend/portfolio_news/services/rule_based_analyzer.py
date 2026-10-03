"""
Zero-cost deterministic news analysis for PWMS.

This module deliberately uses no LLM/API. It converts the metadata already
retrieved from free RSS sources into the same ArticleAnalysis shape consumed
by the existing alert pipeline. It is intentionally conservative: uncertain
stories receive lower confidence/impact rather than invented facts.
"""

import re
from dataclasses import dataclass
from typing import Optional

from ..constants import ImpactLevel, Materiality, NewsCategory, Sentiment, TimeHorizon


_WORD_RE = re.compile(r"[a-z0-9$&/-]+", re.IGNORECASE)


def _tokens(text: str) -> set[str]:
    return {token.lower() for token in _WORD_RE.findall(text or "")}


def _contains_any(text: str, phrases: tuple[str, ...]) -> bool:
    value = (text or "").lower()
    return any(phrase in value for phrase in phrases)


def _score_hits(text: str, phrases: tuple[str, ...]) -> int:
    value = (text or "").lower()
    return sum(1 for phrase in phrases if phrase in value)


@dataclass
class ArticleAnalysis:
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


class RuleBasedArticleAnalyzer:
    """Classify and score portfolio news with transparent local rules."""

    CATEGORY_RULES = (
        (NewsCategory.EARNINGS, ("earnings", "quarterly results", "profit", "revenue", "ebitda", "eps")),
        (NewsCategory.REGULATORY, ("sebi", "rbi", "regulator", "regulatory", "compliance", "license")),
        (NewsCategory.LEGAL, ("lawsuit", "litigation", "court", "legal", "fraud", "penalty", "fine")),
        (NewsCategory.MANAGEMENT, ("ceo", "cfo", "md & ceo", "managing director", "resigns", "appointed")),
        (NewsCategory.M_AND_A, ("acquisition", "acquires", "merger", "merges", "takeover", "stake purchase")),
        (NewsCategory.PRODUCT, ("launch", "product", "drug approval", "approval", "new model")),
        (NewsCategory.CONTRACT, ("contract", "agreement", "partnership", "deal")),
        (NewsCategory.ORDER, ("order win", "order book", "wins order", "contract win", "large order")),
        (NewsCategory.CORPORATE_GOVERNANCE, ("governance", "independent director", "board", "auditor")),
        (NewsCategory.PROMOTER, ("promoter", "pledge", "promoter holding", "insider")),
        (NewsCategory.CAPITAL_ALLOCATION, ("dividend", "buyback", "fund raise", "fundraising", "rights issue", "capex")),
        (NewsCategory.ANALYST, ("upgrade", "downgrade", "target price", "brokerage", "analyst")),
        (NewsCategory.INDUSTRY, ("industry", "sector", "market share", "demand")),
        (NewsCategory.MACRO, ("rbi", "interest rate", "inflation", "tariff", "crude oil", "usd/inr", "bond yield")),
    )

    POSITIVE = (
        "beats estimates", "beat estimates", "profit rises", "profit jumps",
        "revenue rises", "strong growth", "order win", "wins order",
        "upgrade", "dividend", "buyback", "approval", "record profit",
        "raises guidance", "positive outlook", "margin expands",
    )
    NEGATIVE = (
        "misses estimates", "missed estimates", "profit falls", "profit drops",
        "revenue falls", "downgrade", "penalty", "fine", "fraud", "probe",
        "investigation", "lawsuit", "resigns", "plant shutdown", "warning",
        "margin contracts", "cuts guidance", "negative outlook", "default",
    )
    HIGH_IMPACT = (
        "fraud", "default", "insolvency", "bankruptcy", "license cancelled",
        "license revoked", "major penalty", "material litigation", "accounting irregularity",
        "regulatory action", "merger", "acquisition", "takeover", "ceo resigns",
    )
    MEDIUM_IMPACT = (
        "earnings", "guidance", "order win", "large order", "dividend", "buyback",
        "approval", "fund raise", "downgrade", "upgrade", "lawsuit", "investigation",
    )
    LONG_HORIZON = ("capex", "capacity expansion", "long-term", "strategic investment", "new plant")
    SHORT_HORIZON = ("today", "surges", "falls", "intraday", "breaking", "immediate", "effective immediately")

    def _category(self, text: str) -> str:
        best = (0, NewsCategory.OTHER)
        for category, phrases in self.CATEGORY_RULES:
            hits = _score_hits(text, phrases)
            if hits > best[0]:
                best = (hits, category)
        return best[1]

    def _sentiment(self, text: str) -> str:
        positive = _score_hits(text, self.POSITIVE)
        negative = _score_hits(text, self.NEGATIVE)
        if positive and negative:
            return Sentiment.MIXED
        if positive:
            return Sentiment.POSITIVE
        if negative:
            return Sentiment.NEGATIVE
        return Sentiment.NEUTRAL

    def _impact_score(self, text: str, source_quality: Optional[str]) -> int:
        high = _score_hits(text, self.HIGH_IMPACT)
        medium = _score_hits(text, self.MEDIUM_IMPACT)
        score = 20 + min(high, 3) * 25 + min(medium, 4) * 10

        quality = (source_quality or "").lower()
        if "tier_1" in quality:
            score += 8
        elif "tier_2" in quality:
            score += 4

        return max(0, min(100, score))

    def _materiality(self, impact_score: int) -> str:
        if impact_score >= 81:
            return Materiality.CRITICAL
        if impact_score >= 61:
            return Materiality.HIGH
        if impact_score >= 41:
            return Materiality.MODERATE
        if impact_score >= 21:
            return Materiality.LOW
        return Materiality.TRIVIAL

    def _horizon(self, text: str, category: str) -> str:
        if _contains_any(text, self.SHORT_HORIZON):
            return TimeHorizon.SHORT_TERM
        if category in {
            NewsCategory.EARNINGS, NewsCategory.REGULATORY, NewsCategory.LEGAL,
            NewsCategory.M_AND_A, NewsCategory.MANAGEMENT,
        }:
            return TimeHorizon.MEDIUM_TERM
        if _contains_any(text, self.LONG_HORIZON):
            return TimeHorizon.LONG_TERM
        return TimeHorizon.UNSPECIFIED

    def analyze(self, article, holding, user=None) -> ArticleAnalysis:
        title = (getattr(article, "title", "") or "").strip()
        description = (getattr(article, "description", "") or "").strip()
        source = (getattr(article, "source", "") or "").strip()
        matched_query = (getattr(article, "matched_query", "") or "").strip()
        text = " ".join(part for part in (title, description) if part)
        lowered = text.lower()

        holding_terms = [
            getattr(holding, "display_name", ""),
            getattr(holding, "symbol", ""),
            getattr(holding, "amc_name", ""),
        ]
        holding_hits = sum(
            1 for term in holding_terms
            if term and term.strip().lower() in lowered
        )

        query_is_broad = _contains_any(
            matched_query,
            ("sector", "rbi", "interest rates", "usd/inr", "crude oil", "inflation", "tariffs"),
        )
        relevance_score = 60 + min(holding_hits, 2) * 15
        if matched_query and not query_is_broad:
            relevance_score += 10
        relevance_score = min(100, relevance_score)

        impact_score = self._impact_score(text, getattr(article, "source_quality", None))
        category = self._category(lowered)
        sentiment = self._sentiment(lowered)
        materiality = self._materiality(impact_score)
        impact = ImpactLevel.from_score(impact_score)

        confidence = 0.78
        if not description:
            confidence -= 0.12
        if not source or source.lower() == "google news":
            confidence -= 0.08
        if query_is_broad and holding_hits == 0:
            confidence -= 0.08
        confidence = max(0.35, min(0.95, confidence))

        relevant = relevance_score >= 50 and impact_score >= 20

        summary = title
        if description:
            snippet = re.sub(r"\s+", " ", description).strip()
            if snippet and snippet.lower() not in title.lower():
                summary = f"{title} — {snippet[:350]}"

        implication = (
            f"This {category.lower().replace('_', ' ')} item may affect "
            f"{getattr(holding, 'display_name', 'the holding')}; the rule-based "
            f"score reflects only the supplied headline/snippet and source metadata."
        )
        reason = (
            "Matched through the portfolio-news deterministic matcher and "
            "classified using transparent keyword/event rules."
        )
        key_facts = summary
        interpretation = (
            f"The article contains {sentiment} signals and an estimated "
            f"impact score of {impact_score}/100 under the local rule set."
        )
        uncertainty = (
            "No full article body or external fact verification is performed. "
            "The classification may miss context, sarcasm, corrections, or "
            "information not present in the RSS headline/snippet."
        )

        return ArticleAnalysis(
            relevant=relevant,
            relevance_score=relevance_score,
            sentiment=sentiment,
            impact=impact,
            impact_score=impact_score,
            category=category,
            time_horizon=self._horizon(lowered, category),
            summary=summary[:1000],
            portfolio_implication=implication,
            reason=reason,
            confidence=confidence,
            materiality=materiality,
            key_facts=key_facts[:1500],
            interpretation=interpretation,
            uncertainty_notes=uncertainty,
        )

    def analyze_batch(self, article_holding_pairs, user=None) -> dict:
        results = {}
        for pair in article_holding_pairs:
            article, holding = pair[:2]
            results[(article.id, holding.holding_type, holding.holding_id)] = self.analyze(
                article, holding, user=user
            )
        return results
