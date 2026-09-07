import logging

from datetime import datetime, timezone

from typing import (
    List,
    Optional,
)
from urllib.parse import quote_plus

import feedparser
import requests

from .news_provider import (
    NewsArticleResult,
    NewsProvider,
)


logger = logging.getLogger(__name__)


class GoogleNewsRSSProvider(NewsProvider):
    """
    Free news retrieval via Google News RSS search.

    No API key required. Returns headline/URL/source/snippet
    metadata only — never full article bodies, per PWMS
    copyright/ToS constraints.

    Respects a request timeout and never raises: any failure
    (network, malformed feed, etc.) is logged and results in
    an empty result list so one bad query cannot interrupt
    monitoring of the user's other holdings.
    """

    BASE_URL = "https://news.google.com/rss/search"

    REQUEST_TIMEOUT_SECONDS = 15

    USER_AGENT = (
        "Mozilla/5.0 (compatible; PWMS-PortfolioNewsAgent/1.0; "
        "+https://github.com/avviiiral/Personal_Wealth_Monitoring)"
    )

    def __init__(
        self,
        language="en-IN",
        country="IN",
    ):
        self.language = language
        self.country = country

    def _build_url(
        self,
        query: str,
        from_date: Optional[datetime],
        to_date: Optional[datetime],
    ) -> str:

        search_terms = query

        if from_date is not None:
            search_terms += f" after:{from_date.strftime('%Y-%m-%d')}"

        if to_date is not None:
            search_terms += f" before:{to_date.strftime('%Y-%m-%d')}"

        encoded_query = quote_plus(search_terms)

        ceid = f"{self.country}:{self.language.split('-')[0]}"

        return (
            f"{self.BASE_URL}?q={encoded_query}"
            f"&hl={self.language}&gl={self.country}&ceid={ceid}"
        )

    @staticmethod
    def _parse_published_at(entry) -> Optional[datetime]:

        published_struct = getattr(
            entry,
            "published_parsed",
            None,
        )

        if not published_struct:
            return None

        try:
            return datetime(
                *published_struct[:6],
                tzinfo=timezone.utc,
            )
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _parse_source(entry) -> str:

        source = getattr(entry, "source", None)

        if isinstance(source, dict):
            return source.get("title", "") or ""

        if source is not None and hasattr(source, "title"):
            return source.title or ""

        return ""

    def search(
        self,
        query: str,
        from_date: Optional[datetime] = None,
        to_date: Optional[datetime] = None,
    ) -> List[NewsArticleResult]:

        if not query or not query.strip():
            return []

        url = self._build_url(query, from_date, to_date)

        try:
            response = requests.get(
                url,
                headers={"User-Agent": self.USER_AGENT},
                timeout=self.REQUEST_TIMEOUT_SECONDS,
            )

            response.raise_for_status()

        except requests.exceptions.RequestException as exc:
            logger.warning(
                "GoogleNewsRSSProvider: request failed for "
                "query=%r: %s",
                query,
                exc,
            )
            return []

        try:
            feed = feedparser.parse(response.content)
        except Exception as exc:
            logger.warning(
                "GoogleNewsRSSProvider: failed to parse feed "
                "for query=%r: %s",
                query,
                exc,
            )
            return []

        results = []

        for entry in getattr(feed, "entries", []):

            try:
                title = getattr(entry, "title", "").strip()

                link = getattr(entry, "link", "").strip()

                if not title or not link:
                    continue

                results.append(
                    NewsArticleResult(
                        title=title,
                        url=link,
                        source=self._parse_source(entry) or "Google News",
                        description=getattr(
                            entry, "summary", ""
                        ).strip(),
                        published_at=self._parse_published_at(entry),
                        matched_query=query,
                    )
                )

            except Exception as exc:
                # Skip a single malformed entry, keep the rest.
                logger.debug(
                    "GoogleNewsRSSProvider: skipped malformed "
                    "entry for query=%r: %s",
                    query,
                    exc,
                )
                continue

        return results