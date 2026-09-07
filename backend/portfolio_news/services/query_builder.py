from typing import List, Optional

from .holdings_registry import MonitoredHolding


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

    EVENT_QUERY_SUFFIXES = [
        "earnings",
        "regulatory",
        "acquisition",
        "management",
        "litigation",
        "order",
    ]

    MAX_QUERIES_PER_HOLDING = 10

    MIN_SYMBOL_LENGTH_FOR_STANDALONE_QUERY = 3

    SECTOR_QUERY_TEMPLATE = "Indian {sector} sector"

    MAX_MACRO_QUERIES_PER_HOLDING = 2

    # Sector keyword (matched as a case-insensitive substring of
    # the holding's sector/category string) -> macro topics that
    # defensibly affect that sector. Deliberately curated and
    # narrow per the spec's "Only associate macro news with a
    # holding when there is a defensible relationship" rule -
    # this is not an exhaustive macro-topic list, just the ones
    # with a clear causal story for each sector.
    MACRO_TOPICS_BY_SECTOR = {
        "bank": ["RBI", "interest rates", "bond yields"],
        "financial": ["RBI", "interest rates"],
        "nbfc": ["RBI", "interest rates"],
        "insurance": ["interest rates", "IRDAI"],
        "it": ["USD/INR"],
        "software": ["USD/INR"],
        "technology": ["USD/INR"],
        "oil": ["crude oil prices"],
        "energy": ["crude oil prices"],
        "gas": ["crude oil prices"],
        "power": ["crude oil prices"],
        "auto": ["fuel prices"],
        "automobile": ["fuel prices"],
        "pharma": ["USFDA"],
        "healthcare": ["USFDA"],
        "fmcg": ["inflation"],
        "consumer": ["inflation"],
        "metal": ["commodity prices"],
        "mining": ["commodity prices"],
        "steel": ["commodity prices"],
        "infrastructure": ["government infrastructure policy"],
        "construction": ["government infrastructure policy"],
        "cement": ["government infrastructure policy"],
        "export": ["USD/INR", "tariffs"],
        "textile": ["tariffs"],
        "real estate": ["interest rates"],
    }

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

        sector_query = cls.sector_query(holding)

        if sector_query:
            queries.append(sector_query)

        queries.extend(cls.macro_terms_for_sector(holding.sector))

        # Deduplicate while preserving order, then enforce the cap.
        deduplicated = list(dict.fromkeys(queries))

        return deduplicated[: cls.MAX_QUERIES_PER_HOLDING]