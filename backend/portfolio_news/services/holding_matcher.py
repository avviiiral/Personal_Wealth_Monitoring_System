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
    def is_relevant(
        title: str,
        description: str,
        holding: MonitoredHolding,
        matched_query: str = "",
    ) -> bool:

        # Deterministic matching considers both the headline and the
        # provider-supplied article body/description. This keeps the
        # matcher broader than headline-only matching without requiring
        # a second full-article fetch.
        searchable = _build_searchable_text(title, description)

        for term in holding.identifier_terms():

            if len(term) < MIN_TERM_LENGTH_FOR_MATCH:
                continue

            if _contains_phrase(searchable, term):
                return True

        if (
            holding.symbol
            and len(holding.symbol) >= 2
            and _contains_phrase(searchable, holding.symbol)
        ):
            return True

        if (
            holding.isin
            and len(holding.isin) >= MIN_ISIN_LENGTH
            and holding.isin.lower() in searchable
        ):
            return True

        # Uploaded underlying holdings are an explicit portfolio
        # relationship. Only accept an underlying match when the
        # article text contains that underlying's name/ISIN and the
        # search query was generated for that same underlying.
        underlying = QueryBuilder.underlying_for_query(
            matched_query,
            holding,
        )
        if underlying is not None:
            underlying_name = (underlying.name or "").strip()
            if underlying_name and _contains_phrase(searchable, underlying_name):
                return True
            if (
                underlying.isin
                and len(underlying.isin) >= MIN_ISIN_LENGTH
                and underlying.isin.lower() in searchable
            ):
                return True

        # Sector/macro fallback: a genuine macro or sector story
        # (e.g. "RBI raises repo rate") will never mention a
        # specific company by name, so the checks above are
        # expected to miss it. That's only acceptable when the
        # query that surfaced this article was itself generated
        # specifically for this holding's sector (see
        # QueryBuilder.is_sector_or_macro_query) - i.e. the
        # relationship was established deliberately at query time,
        # not guessed after the fact. Even then, the article text
        # must still mention the sector or the specific macro topic
        # searched for, so an off-topic result from that query
        # doesn't get waved through untested.
        if matched_query and holding.sector:
            if QueryBuilder.is_sector_or_macro_query(
                matched_query, holding
            ):
                sector = holding.sector.strip()

                if sector and sector.lower() in searchable:
                    return True

                if matched_query.lower() in searchable:
                    return True

        return False

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