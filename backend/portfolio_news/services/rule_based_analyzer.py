"""
Zero-cost deterministic news analysis for PWMS.

This module deliberately uses no LLM/API. It converts the metadata already
retrieved from free RSS sources into the same ArticleAnalysis shape consumed
by the existing alert pipeline. It is intentionally conservative: uncertain
stories receive lower confidence/impact rather than invented facts.
"""

import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Optional

from config.pwms_config import get as get_pwms_config

from ..constants import ImpactLevel, Materiality, NewsCategory, Sentiment, TimeHorizon


_WORD_RE = re.compile(r"[a-z0-9$&/-]+", re.IGNORECASE)

# Optional inflected endings so "fine" also matches "fined" and "probe"
# matches "probes", without matching inside unrelated words
# ("fine" must not match "refinery", "eps" must not match "steps").
_INFLECTION = r"(?:s|es|d|ed)?"

# A phrase is treated as negated when one of these words appears within
# the three words before it ("no fraud found", "denies penalty").
_NEGATIONS = frozenset(
    {
        "no", "not", "none", "without", "never",
        "deny", "denies", "denied", "refutes", "rejects", "rejected",
    }
)


def _tokens(text: str) -> set[str]:
    return {token.lower() for token in _WORD_RE.findall(text or "")}


@lru_cache(maxsize=1024)
def _phrase_regex(phrase: str):
    return re.compile(
        r"(?<![a-z0-9])" + re.escape(phrase.strip().lower()) + _INFLECTION + r"(?![a-z0-9])"
    )


def _is_negated(value: str, start: int) -> bool:
    words = re.findall(r"[a-z']+", value[max(0, start - 40):start])[-3:]
    return any(word in _NEGATIONS for word in words)


def _has_phrase(text: str, phrase: str, honor_negation: bool = False) -> bool:
    """Whole-word phrase match, optionally ignoring negated occurrences."""

    if not phrase or not phrase.strip():
        return False

    value = (text or "").lower()

    for match in _phrase_regex(phrase).finditer(value):
        if not (honor_negation and _is_negated(value, match.start())):
            return True

    return False


def _contains_any(text: str, phrases: tuple[str, ...]) -> bool:
    return any(_has_phrase(text, phrase) for phrase in phrases)


def _score_hits(text: str, phrases: tuple[str, ...], honor_negation: bool = False) -> int:
    return sum(1 for phrase in phrases if _has_phrase(text, phrase, honor_negation))


def _allow_sector_context() -> bool:
    """Sector/macro stories that never name the holding are hidden unless enabled."""

    return bool(get_pwms_config("news", "allow_sector_context", False))


def _format_inr(amount) -> str:
    """Format a rupee amount with Indian digit grouping (e.g. 12,34,567)."""

    try:
        number = int(round(float(amount)))
    except (TypeError, ValueError):
        return ""

    sign = "-" if number < 0 else ""
    digits = str(abs(number))

    if len(digits) > 3:
        head, tail = digits[:-3], digits[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        digits = ",".join(groups + [tail])

    return f"\u20b9{sign}{digits}"


_CATEGORY_CONSEQUENCE = {
    NewsCategory.EARNINGS: "can change expectations for the company's profit and valuation",
    NewsCategory.REGULATORY: "can affect operations, compliance costs or investor confidence",
    NewsCategory.LEGAL: "can add legal cost or liability risk",
    NewsCategory.MANAGEMENT: "can signal a change in leadership or strategy",
    NewsCategory.M_AND_A: "can change the company's size, ownership or balance sheet",
    NewsCategory.PRODUCT: "can affect future revenue if the product succeeds or is delayed",
    NewsCategory.CONTRACT: "can affect future revenue visibility",
    NewsCategory.ORDER: "can affect future revenue visibility",
    NewsCategory.CORPORATE_GOVERNANCE: "can bear on how the company is run and overseen",
    NewsCategory.PROMOTER: "can reflect promoter confidence or financing pressure",
    NewsCategory.CAPITAL_ALLOCATION: "can affect payouts, funding or capital spending",
    NewsCategory.ANALYST: "reflects an analyst's opinion rather than a company event",
    NewsCategory.INDUSTRY: "describes sector conditions rather than company-specific news",
    NewsCategory.MACRO: "describes broad economic conditions",
    NewsCategory.OTHER: "may be relevant, but its effect is unclear from the headline alone",
}


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
    # Critical events should be capable of reaching CRITICAL even for
    # smaller portfolio weights. These are intentionally limited to events
    # that can materially change the risk profile of a holding.
    CRITICAL_IMPACT = (
        "fraud", "accounting fraud", "accounting irregularity",
        "insolvency", "bankruptcy", "default", "debt default",
        "license cancelled", "license revoked", "business licence cancelled",
        "major regulatory action", "regulatory ban", "trading ban",
    )
    HIGH_IMPACT = (
        "major penalty", "material litigation", "regulatory action",
        "merger", "acquisition", "takeover", "ceo resigns", "cfo resigns",
        "guidance cut", "cuts guidance", "withdraws guidance",
        "plant shutdown", "business shutdown", "major order win",
        "large order win", "significant order", "material order",
        "major contract", "material contract", "promoter pledge",
        "pledge increase", "major investigation",
    )
    MEDIUM_IMPACT = (
        "earnings", "quarterly results", "guidance", "order win",
        "large order", "dividend", "buyback", "approval", "fund raise",
        "downgrade", "upgrade", "lawsuit", "investigation", "probe",
        "profit falls", "profit drops", "revenue falls", "revenue drops",
        "margin contracts", "margin falls", "plant closure", "penalty",
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
        positive = _score_hits(text, self.POSITIVE, honor_negation=True)
        negative = _score_hits(text, self.NEGATIVE, honor_negation=True)
        if positive and negative:
            return Sentiment.MIXED
        if positive:
            return Sentiment.POSITIVE
        if negative:
            return Sentiment.NEGATIVE
        return Sentiment.NEUTRAL

    def _impact_score(self, text: str, source_quality: Optional[str]) -> int:
        critical = _score_hits(text, self.CRITICAL_IMPACT, honor_negation=True)
        high = _score_hits(text, self.HIGH_IMPACT, honor_negation=True)

        # Avoid double-counting a generic medium signal when it is already
        # represented by a more specific high-impact phrase. For example,
        # "major investigation" should not score both "major investigation"
        # and the generic "investigation" rule; likewise "withdraws guidance"
        # should not receive an additional generic "guidance" point.
        medium = sum(
            1
            for phrase in self.MEDIUM_IMPACT
            if _has_phrase(text, phrase, honor_negation=True)
            and not any(
                phrase in high_phrase and _has_phrase(text, high_phrase)
                for high_phrase in self.HIGH_IMPACT
            )
        )

        # Keep ordinary earnings/orders/news in the moderate range while
        # allowing clearly material events to become HIGH/CRITICAL.
        score = (
            20
            + min(critical, 2) * 38
            + min(high, 3) * 22
            + min(medium, 4) * 8
        )

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

    @staticmethod
    def _direct_matches(lowered: str, holding) -> list[str]:
        """Names, aliases, symbol, ISIN or AMC of the holding found as whole words."""

        candidates = [
            getattr(holding, "display_name", ""),
            getattr(holding, "symbol", ""),
            getattr(holding, "amc_name", ""),
            getattr(holding, "isin", ""),
            *(getattr(holding, "aliases", None) or []),
        ]

        found = []
        for term in candidates:
            term = (term or "").strip()
            if len(term) >= 3 and term.lower() not in {f.lower() for f in found}:
                if _has_phrase(lowered, term):
                    found.append(term)

        return found

    @staticmethod
    def _underlying_match(lowered: str, holding):
        """A disclosed underlying stock of a fund holding that the text names."""

        for underlying in getattr(holding, "underlyings", None) or []:
            name = (getattr(underlying, "name", "") or "").strip()
            isin = (getattr(underlying, "isin", "") or "").strip()
            if (len(name) >= 3 and _has_phrase(lowered, name)) or (
                len(isin) >= 8 and _has_phrase(lowered, isin)
            ):
                return underlying

        return None

    @staticmethod
    def _explain(
        holding,
        category,
        sentiment,
        impact,
        impact_score,
        direct_terms,
        underlying,
        query_is_broad,
    ) -> tuple[str, str, str]:
        """Plain-language reason / portfolio implication / interpretation.

        Uses only the holding's own portfolio numbers; nothing is invented.
        Indirect (fund underlying) exposure is labelled as an estimate.
        """

        name = getattr(holding, "display_name", "") or "this holding"

        try:
            weight = float(getattr(holding, "portfolio_weight", 0) or 0)
        except (TypeError, ValueError):
            weight = 0.0

        value = _format_inr(getattr(holding, "current_value", 0)) if getattr(
            holding, "current_value", 0
        ) else ""

        position = f"{weight:.1f}% of your portfolio" if weight > 0 else "part of your portfolio"
        if value:
            position += f" (about {value})"

        category_label = str(category).lower().replace("_", " ")
        consequence = _CATEGORY_CONSEQUENCE.get(category, _CATEGORY_CONSEQUENCE[NewsCategory.OTHER])

        if weight >= 10:
            size_note = "This is a large position, so the news deserves attention."
        elif weight >= 3:
            size_note = "This is a mid-sized position."
        elif weight > 0:
            size_note = "This is a small position, so the portfolio-level effect is limited."
        else:
            size_note = ""

        if underlying is not None:
            underlying_name = getattr(underlying, "name", "") or "an underlying stock"
            try:
                underlying_weight = float(getattr(underlying, "weight", 0) or 0)
            except (TypeError, ValueError):
                underlying_weight = 0.0

            reason = (
                f"Selected because it names {underlying_name}, a disclosed holding of "
                f"{name}. This is an indirect link based on the fund's last disclosed "
                "portfolio, not a confirmed current holding."
            )
            exposure = ""
            if underlying_weight > 0:
                exposure = (
                    f" {underlying_name} is {underlying_weight:.2f}% of {name} in its "
                    "last disclosed holdings"
                )
                try:
                    estimate = float(getattr(holding, "current_value", 0) or 0) * underlying_weight / 100
                except (TypeError, ValueError):
                    estimate = 0.0
                if estimate > 0:
                    exposure += f", so roughly {_format_inr(estimate)} of your holding is indirectly exposed (estimate)"
                exposure += "."
            implication = (
                f"You hold {name}, which is {position}. This {category_label} story about "
                f"{underlying_name} {consequence}.{exposure} {size_note}"
            ).strip()
        elif direct_terms:
            reason = f"Selected because the headline or snippet names {direct_terms[0]} directly."
            implication = (
                f"{name} is {position}. This {category_label} story {consequence}. {size_note}"
            ).strip()
        elif query_is_broad:
            reason = (
                f"Sector or market-wide story. It does not name {name}, so the link is "
                "indirect and unconfirmed."
            )
            implication = (
                f"This {category_label} story {consequence}. It does not mention {name}, "
                "so any effect on it is indirect."
            )
        else:
            reason = f"Fetched while searching for {name}, but the text does not name it."
            implication = "The headline does not name this holding, so its relevance is unclear."

        interpretation = (
            f"Keyword-based tone: {sentiment}. Estimated impact {impact_score}/100 "
            f"({str(impact).lower().replace('_', ' ')}) under the local rule set. "
            "This is a rule-based reading of the headline and snippet, not a prediction "
            "of price movement."
        )

        return reason, implication, interpretation

    def analyze(self, article, holding, user=None) -> ArticleAnalysis:
        title = (getattr(article, "title", "") or "").strip()
        description = (getattr(article, "description", "") or "").strip()
        source = (getattr(article, "source", "") or "").strip()
        matched_query = (getattr(article, "matched_query", "") or "").strip()
        text = " ".join(part for part in (title, description) if part)
        lowered = text.lower()

        direct_terms = self._direct_matches(lowered, holding)
        underlying = self._underlying_match(lowered, holding)
        holding_hits = len(direct_terms) + (1 if underlying is not None else 0)

        query_is_broad = _contains_any(
            matched_query,
            ("sector", "rbi", "interest rates", "usd/inr", "crude oil", "inflation", "tariffs"),
        )

        if holding_hits:
            # The text itself names the holding (or a disclosed underlying).
            relevance_score = 60 + min(holding_hits, 2) * 15
            if matched_query and not query_is_broad:
                relevance_score += 10
        elif query_is_broad:
            # Sector/macro context that never names the holding. Hidden from
            # the feed unless explicitly enabled.
            relevance_score = 50 if _allow_sector_context() else 35
        else:
            # Fetched for this holding, but the text does not name it.
            relevance_score = 40
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

        reason, implication, interpretation = self._explain(
            holding=holding,
            category=category,
            sentiment=sentiment,
            impact=impact,
            impact_score=impact_score,
            direct_terms=direct_terms,
            underlying=underlying,
            query_is_broad=query_is_broad,
        )
        key_facts = summary
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
