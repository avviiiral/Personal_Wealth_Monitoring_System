import logging

from abc import (
    ABC,
    abstractmethod,
)

from dataclasses import dataclass

from datetime import datetime

from typing import (
    List,
    Optional,
)


logger = logging.getLogger(__name__)


@dataclass
class NewsArticleResult:
    """
    A single article returned by a NewsProvider.

    This is intentionally metadata-only. Providers must not
    return full copyrighted article bodies — only headline,
    URL, source, publication time, and a short snippet.
    """

    title: str

    url: str

    source: str

    description: str = ""

    published_at: Optional[datetime] = None

    # The search query that surfaced this article. Kept for
    # debugging/logging, not persisted on the stored article.
    matched_query: str = ""


class NewsProvider(ABC):
    """
    Abstract news retrieval provider.

    The rest of the portfolio_news app depends only on this
    interface. A future provider (NewsAPI, GNews, a paid
    financial-news API, etc.) can be added by implementing
    this class — no other code needs to change.
    """

    @abstractmethod
    def search(
        self,
        query: str,
        from_date: Optional[datetime] = None,
        to_date: Optional[datetime] = None,
    ) -> List[NewsArticleResult]:
        """
        Search for articles matching `query`.

        Implementations must fail gracefully: on any network,
        parsing, or provider-side error, log the problem and
        return an empty list rather than raising. A single
        failed query must never take down the monitoring run.
        """

        raise NotImplementedError