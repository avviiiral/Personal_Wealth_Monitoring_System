from typing import List, Optional

from .holdings_registry import MonitoredHolding
from config.pwms_config import get as get_pwms_config


class QueryBuilder:
    """
    Turns one monitored holding into a small set of search
    queries.

    Kept deliberately bounded: one broad company-name query
    (catches most relevant news) plus a curated set of
    event-type queries that map to the categories most likely
    to be CRITICAL/HIGH impact (regulatory, legal, management,
    M&A, earnings, orders) - not all ~15 possible suffixes for
    every holding, which would generate hundreds of requests
    across a real portfolio for little extra recall.

    Also generates at most one sector query and a small, sector-
    specific set of macro queries (see MACRO_TOPICS_BY_SECTOR).
    These are intentionally NOT holding-name-specific text - a
    macro story about RBI policy will never mention a particular
    bank by name - so HoldingMatcher treats them differently
    (see is_sector_or_macro_query below): the defensible
    relationship to this holding is established here, at query
    generation time, by deriving the query from this holding's
    own sector, rather than by post-hoc text matching against
    the company name.
    """

    EVENT_QUERY_SUFFIXES = get_pwms_config("news", "event_query_suffixes", [])
    MAX_QUERIES_PER_HOLDING = get_pwms_config("news", "max_queries_per_holding", 15)
    MAX_UNDERLYING_QUERIES_PER_HOLDING = get_pwms_config("news", "max_underlying_queries_per_holding", 5)
    MIN_SYMBOL_LENGTH_FOR_STANDALONE_QUERY = get_pwms_config("news", "min_symbol_length_for_standalone_query", 3)
    SECTOR_QUERY_TEMPLATE = get_pwms_config("news", "sector_query_template", "Indian {sector} sector")
    MAX_MACRO_QUERIES_PER_HOLDING = get_pwms_config("news", "max_macro_queries_per_holding", 2)
    MACRO_TOPICS_BY_SECTOR = get_pwms_config("news", "macro_topics_by_sector", {})

    @classmethod
    def macro_terms_for_sector(cls, sector: str) -> List[str]:
        """
        Sector-appropriate macro query terms, capped at
        MAX_MACRO_QUERIES_PER_HOLDING. Returns [] for an unknown
        or empty sector rather than guessing - no macro query is
        safer than a spurious one.
        """

        sector_lower = (sector or "").strip().lower()

        if not sector_lower:
            return []

        terms: List[str] = []

        for keyword, macro_terms in cls.MACRO_TOPICS_BY_SECTOR.items():
            if keyword in sector_lower:
                for term in macro_terms:
                    if term not in terms:
                        terms.append(term)

        return terms[: cls.MAX_MACRO_QUERIES_PER_HOLDING]

    @classmethod
    def sector_query(cls, holding: MonitoredHolding) -> Optional[str]:
        sector = (holding.sector or "").strip()

        if not sector:
            return None

        return cls.SECTOR_QUERY_TEMPLATE.format(sector=sector)

    @classmethod
    def is_sector_or_macro_query(
        cls,
        query: str,
        holding: MonitoredHolding,
    ) -> bool:
        """
        True if `query` is the sector or a macro query this
        class would generate for `holding` - used by
        HoldingMatcher to know when an article's lack of a
        company-name mention is expected, not a sign it's
        irrelevant.
        """

        if not holding.sector:
            return False

        if query == cls.sector_query(holding):
            return True

        return query in cls.macro_terms_for_sector(holding.sector)

    @classmethod
    def underlying_queries(cls, holding: MonitoredHolding) -> List[str]:
        """Return bounded queries for the largest uploaded underlyings."""
        seen = set()
        queries = []
        for underlying in holding.underlyings:
            name = (underlying.name or "").strip()
            if not name or name.lower() in seen:
                continue
            seen.add(name.lower())
            queries.append(name)
            if len(queries) >= cls.MAX_UNDERLYING_QUERIES_PER_HOLDING:
                break
        return queries

    @classmethod
    def underlying_for_query(cls, query: str, holding: MonitoredHolding):
        query_lower = (query or "").strip().lower()
        if not query_lower:
            return None
        for underlying in holding.underlyings:
            if (underlying.name or "").strip().lower() == query_lower:
                return underlying
        return None

    @classmethod
    def build_queries(cls, holding: MonitoredHolding) -> List[str]:

        queries: List[str] = []

        primary_term = holding.display_name.strip()

        if not primary_term:
            return []

        queries.append(primary_term)

        for suffix in cls.EVENT_QUERY_SUFFIXES:
            queries.append(f"{primary_term} {suffix}")

        symbol = holding.symbol.strip()

        if (
            symbol
            and len(symbol) >= cls.MIN_SYMBOL_LENGTH_FOR_STANDALONE_QUERY
            and symbol.lower() != primary_term.lower()
        ):
            queries.append(f"{symbol} share")

        # Explicit underlying relationships take priority over broad
        # sector/macro queries.
        queries.extend(cls.underlying_queries(holding))

        sector_query = cls.sector_query(holding)

        if sector_query:
            queries.append(sector_query)

        queries.extend(cls.macro_terms_for_sector(holding.sector))

        # Deduplicate while preserving order, then enforce the cap.
        deduplicated = list(dict.fromkeys(queries))

        return deduplicated[: cls.MAX_QUERIES_PER_HOLDING]