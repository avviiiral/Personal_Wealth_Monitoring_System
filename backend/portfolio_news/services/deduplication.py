import hashlib

from datetime import timedelta

from difflib import SequenceMatcher

from typing import Optional

from django.utils import timezone

from .news_provider import NewsArticleResult
from .text_utils import normalize_title


def compute_url_hash(url: str) -> str:
    return hashlib.sha256(url.strip().encode("utf-8")).hexdigest()


def compute_fingerprint(
    normalized_title: str,
    published_at,
) -> str:
    """
    Fingerprint = normalized title + the calendar date the
    article was published (or "unknown" if no date is
    available). Two articles about the same event on the same
    day, worded almost identically, will collide here.
    """

    date_bucket = (
        published_at.date().isoformat()
        if published_at is not None
        else "unknown"
    )

    raw = f"{normalized_title}|{date_bucket}"

    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def titles_are_similar(
    normalized_a: str,
    normalized_b: str,
    threshold: float = 0.72,
) -> bool:

    if not normalized_a or not normalized_b:
        return False

    ratio = SequenceMatcher(
        None,
        normalized_a,
        normalized_b,
    ).ratio()

    return ratio >= threshold


class ArticleDeduplicator:
    """
    Decides whether a freshly retrieved article is the same
    underlying event as one already stored.

    Three checks, cheapest first:

        1. Exact URL match (same article seen again).
        2. Exact fingerprint match (same normalized headline,
           same publication day - near-certain same event).
        3. Fuzzy title similarity within a recent window
           (different publisher, similar wording, same event -
           e.g. "gets USFDA approval" vs "receives USFDA nod").
    """

    RECENT_WINDOW_DAYS = 3

    NEAR_DUPLICATE_THRESHOLD = 0.72

    @classmethod
    def find_existing(cls, candidate: NewsArticleResult):

        from ..models import NewsArticle

        url_hash = compute_url_hash(candidate.url)

        existing = NewsArticle.objects.filter(
            url_hash=url_hash
        ).first()

        if existing:
            return existing

        normalized_title = normalize_title(candidate.title)

        fingerprint = compute_fingerprint(
            normalized_title,
            candidate.published_at,
        )

        existing = NewsArticle.objects.filter(
            fingerprint=fingerprint
        ).first()

        if existing:
            return existing

        reference_date = (
            candidate.published_at
            or timezone.now()
        )

        window_start = reference_date - timedelta(
            days=cls.RECENT_WINDOW_DAYS
        )

        window_end = reference_date + timedelta(
            days=cls.RECENT_WINDOW_DAYS
        )

        recent_candidates = NewsArticle.objects.filter(
            published_at__gte=window_start,
            published_at__lte=window_end,
        ).only(
            "id",
            "normalized_title",
        )

        for article in recent_candidates:
            if titles_are_similar(
                normalized_title,
                article.normalized_title,
                threshold=cls.NEAR_DUPLICATE_THRESHOLD,
            ):
                return article

        return None