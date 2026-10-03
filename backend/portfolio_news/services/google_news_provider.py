import logging
import time

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

    Reliability behaviour:

    * Transient failures (timeouts, connection errors, HTTP 429 and
      5xx) are retried up to REQUEST_ATTEMPTS times with exponential
      backoff. Other HTTP errors are not retried.
    * After CIRCUIT_BREAKER_THRESHOLD consecutive failed queries the
      provider stops calling Google News for CIRCUIT_BREAKER_COOLDOWN_
      SECONDS, so a full outage fails fast instead of spending
      timeout x retries on every remaining query of the run.
    * `failed_queries` counts queries that ultimately failed so the
      pipeline can report them as provider failures.
    """

    BASE_URL = "https://news.google.com/rss/search"

    REQUEST_TIMEOUT_SECONDS = 15

    REQUEST_ATTEMPTS = 3

    RETRY_BACKOFF_SECONDS = 1.0

    RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})

    CIRCUIT_BREAKER_THRESHOLD = 5

    CIRCUIT_BREAKER_COOLDOWN_SECONDS = 60.0

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

        self.failed_queries = 0
        self._consecutive_failures = 0
        self._circuit_open_until = 0.0

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

    @classmethod
    def _is_retryable(cls, exc: Exception) -> bool:
        if isinstance(
            exc,
            (
                requests.exceptions.Timeout,
                requests.exceptions.ConnectionError,
            ),
        ):
            return True

        if isinstance(exc, requests.exceptions.HTTPError):
            response = getattr(exc, "response", None)
            status_code = getattr(response, "status_code", None)
            return status_code in cls.RETRYABLE_STATUS_CODES

        return False

    def _circuit_is_open(self) -> bool:
        return time.monotonic() < self._circuit_open_until

    def _record_failure(self) -> None:
        self.failed_queries += 1
        self._consecutive_failures += 1

        if self._consecutive_failures >= self.CIRCUIT_BREAKER_THRESHOLD:
            self._circuit_open_until = (
                time.monotonic() + self.CIRCUIT_BREAKER_COOLDOWN_SECONDS
            )
            self._consecutive_failures = 0
            logger.warning(
                "GoogleNewsRSSProvider: %s consecutive queries failed; "
                "pausing requests for %.0f seconds",
                self.CIRCUIT_BREAKER_THRESHOLD,
                self.CIRCUIT_BREAKER_COOLDOWN_SECONDS,
            )

    def _fetch(self, url: str, query: str):
        """
        GET `url`, retrying transient failures. Returns the response,
        or None when the request ultimately failed.
        """
        attempts = max(1, self.REQUEST_ATTEMPTS)

        for attempt in range(attempts):
            try:
                response = requests.get(
                    url,
                    headers={"User-Agent": self.USER_AGENT},
                    timeout=self.REQUEST_TIMEOUT_SECONDS,
                )

                response.raise_for_status()

                return response

            except requests.exceptions.RequestException as exc:
                is_last_attempt = attempt == attempts - 1

                if is_last_attempt or not self._is_retryable(exc):
                    logger.warning(
                        "GoogleNewsRSSProvider: request failed for "
                        "query=%r: %s",
                        query,
                        exc,
                    )
                    return None

                delay = self.RETRY_BACKOFF_SECONDS * (2 ** attempt)

                logger.info(
                    "GoogleNewsRSSProvider: transient failure for "
                    "query=%r (attempt %d/%d): %s; retrying in %.1fs",
                    query,
                    attempt + 1,
                    attempts,
                    exc,
                    delay,
                )

                time.sleep(delay)

        return None

    def search(
        self,
        query: str,
        from_date: Optional[datetime] = None,
        to_date: Optional[datetime] = None,
    ) -> List[NewsArticleResult]:

        if not query or not query.strip():
            return []

        if self._circuit_is_open():
            self.failed_queries += 1
            logger.debug(
                "GoogleNewsRSSProvider: circuit open, skipping "
                "query=%r",
                query,
            )
            return []

        url = self._build_url(query, from_date, to_date)

        response = self._fetch(url, query)

        if response is None:
            self._record_failure()
            return []

        self._consecutive_failures = 0

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