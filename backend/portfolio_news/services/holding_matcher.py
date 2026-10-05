import re

from typing import List

from .holdings_registry import MonitoredHolding
from .query_builder import QueryBuilder


MIN_TERM_LENGTH_FOR_MATCH = 3

MIN_ISIN_LENGTH = 8


def _build_searchable_text(title: str, description: str) -> str:
    """
    Build the deterministic matching surface from the news headline and
    the provider-supplied article body/description.

    Google News RSS does not provide the full publisher article body, so
    the description is the deepest body text available to this layer.
    Gemini still receives the stored article description for enrichment.
    """

    return f"{title or ''} {description or ''}".lower()


def _contains_phrase(haystack_lower: str, phrase: str) -> bool:
    """
    Word-boundary substring match, case-insensitive. Prevents
    false positives like "Tata" matching inside an unrelated
    word, while still matching multi-word company names.
    """

    if not phrase:
        return False

    pattern = r"\b" + re.escape(phrase.lower()) + r"\b"

    return re.search(pattern, haystack_lower) is not None


class HoldingMatcher:
    """
    Deterministic (non-AI) relevance filter.

    Runs before Gemini ever sees an article: cheap Python
    matching on company name, aliases, ticker, and ISIN. Only
    holdings that pass this filter have their articles sent on
    for AI analysis, per PWMS's cost-control requirements.
    """

    @staticmethod
    def match_score(
        title: str,
        description: str,
        holding: MonitoredHolding,
        matched_query: str = "",
    ) -> int:
        """Return an explainable 0-100 deterministic entity-match strength.

        Headline matches deliberately outrank body-only matches. Exact ISIN,
        company name/alias and symbol matches are stronger than sector/macro
        matches, reducing false positives without using an AI model.
        """
        title_text = (title or "").lower()
        body_text = (description or "").lower()

        def score_term(term: str, title_weight: int, body_weight: int) -> int:
            if not term or len(term.strip()) < MIN_TERM_LENGTH_FOR_MATCH:
                return 0
            phrase = term.strip()
            if _contains_phrase(title_text, phrase):
                return title_weight
            if _contains_phrase(body_text, phrase):
                return body_weight
            return 0

        best = max(
            (score_term(term, 100, 70) for term in holding.identifier_terms()),
            default=0,
        )

        if holding.symbol:
            best = max(best, score_term(holding.symbol, 95, 65))

        if holding.isin and len(holding.isin) >= MIN_ISIN_LENGTH:
            if holding.isin.lower() in title_text:
                best = max(best, 100)
            elif holding.isin.lower() in body_text:
                best = max(best, 80)

        underlying = QueryBuilder.underlying_for_query(matched_query, holding)
        if underlying is not None:
            if underlying.name:
                best = max(best, score_term(underlying.name, 85, 60))
            if underlying.isin and len(underlying.isin) >= MIN_ISIN_LENGTH:
                if underlying.isin.lower() in title_text:
                    best = max(best, 90)
                elif underlying.isin.lower() in body_text:
                    best = max(best, 75)

        searchable = _build_searchable_text(title, description)
        if matched_query and holding.sector and QueryBuilder.is_sector_or_macro_query(
            matched_query, holding
        ):
            if holding.sector.lower() in searchable or matched_query.lower() in searchable:
                best = max(best, 50)

        return best

    @classmethod
    def is_relevant(
        cls,
        title: str,
        description: str,
        holding: MonitoredHolding,
        matched_query: str = "",
    ) -> bool:
        return cls.match_score(title, description, holding, matched_query) >= 50

    @classmethod
    def connection_for_article(
        cls,
        title: str,
        description: str,
        holding: MonitoredHolding,
        matched_query: str = "",
    ) -> dict:
        """
        Return the deterministic relationship explaining why an article
        matched this portfolio holding.
        """
        searchable = _build_searchable_text(title, description)

        underlying = QueryBuilder.underlying_for_query(
            matched_query,
            holding,
        )
        if underlying is not None:
            underlying_name = (underlying.name or "").strip()
            if underlying_name and _contains_phrase(searchable, underlying_name):
                return {
                    "connection_type": "underlying",
                    "underlying_name": underlying_name,
                    "underlying_weight": underlying.weight,
                }
            if (
                underlying.isin
                and len(underlying.isin) >= MIN_ISIN_LENGTH
                and underlying.isin.lower() in searchable
            ):
                return {
                    "connection_type": "underlying",
                    "underlying_name": underlying_name,
                    "underlying_weight": underlying.weight,
                }

        return {
            "connection_type": "direct",
            "underlying_name": "",
            "underlying_weight": None,
        }

    @classmethod
    def match_holdings(
        cls,
        title: str,
        description: str,
        holdings: List[MonitoredHolding],
        matched_query: str = "",
    ) -> List[MonitoredHolding]:
        """
        Returns the subset of `holdings` this article is
        deterministically relevant to. Usually zero or one
        item, but a holding-company mention could legitimately
        match more than one holding.
        """

        return [
            holding
            for holding in holdings
            if cls.is_relevant(
                title, description, holding, matched_query
            )
        ]