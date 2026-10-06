import hashlib
import re

from datetime import timedelta

from difflib import SequenceMatcher

from typing import Optional

from django.conf import settings
from django.db.models import Q
from django.utils import timezone

from .news_provider import NewsArticleResult
from .text_utils import normalize_title

_EVENT_FAMILIES = {
    "acquisition": ("acquisition", "acquire", "takeover"),
    "merger": ("merger", "amalgamation"),
    "demerger": ("demerger", "scheme of arrangement"),
    "results": ("results", "profit", "revenue", "ebitda", "eps"),
    "dividend": ("dividend",),
    "buyback": ("buyback", "buy-back"),
    "split": ("stock split", "split of shares"),
    "bonus": ("bonus issue", "bonus shares"),
    "fundraising": ("fund raise", "fundraising", "qip", "preferential issue"),
    "management": ("ceo", "cfo", "resign", "appointment", "director"),
    "regulatory": ("sebi", "rbi", "regulatory", "penalty", "ban", "investigation"),
    "litigation": ("litigation", "lawsuit", "court", "legal proceeding"),
    "order": ("order win", "large order", "material order", "purchase order"),
    "contract": ("contract", "agreement", "partnership"),
    "pledge": ("promoter pledge", "pledge", "pledged"),
}

_STOPWORDS = {
    "the", "and", "of", "for", "to", "a", "an", "with", "on", "in", "from",
    "limited", "ltd", "company", "article", "alert", "news", "report",
    "story", "update", "first", "second", "third", "latest",
}

_EVENT_ENTITY_STOPWORDS = {
    token
    for phrases in _EVENT_FAMILIES.values()
    for phrase in phrases
    for token in re.findall(r"[a-z0-9]+", phrase.lower())
}


def _event_family(text: str) -> str:
    value = (text or "").lower()
    scores = [(sum(1 for phrase in phrases if phrase in value), family) for family, phrases in _EVENT_FAMILIES.items()]
    score, family = max(scores, default=(0, ""))
    return family if score else ""


def _entity_tokens(candidate: NewsArticleResult) -> set[str]:
    raw = " ".join((candidate.matched_query or "", candidate.title or ""))
    return {
        token
        for token in re.findall(r"[a-z0-9]+", raw.lower())
        if len(token) >= 3
        and token not in _STOPWORDS
        and token not in _EVENT_ENTITY_STOPWORDS
    }



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

        normalized_title = normalize_title(candidate.title)

        fingerprint = compute_fingerprint(
            normalized_title,
            candidate.published_at,
        )

        existing = (
            NewsArticle.objects
            .filter(
                Q(url_hash=url_hash)
                | Q(fingerprint=fingerprint)
            )
            .first()
        )

        if existing:
            return existing

        reference_date = (
            candidate.published_at
            or timezone.now()
        )

        window_days = max(1, int(getattr(settings, "NEWS_EVENT_CLUSTER_WINDOW", cls.RECENT_WINDOW_DAYS)))
        window_start = reference_date - timedelta(days=window_days)
        window_end = reference_date + timedelta(days=window_days)

        recent_candidates = NewsArticle.objects.filter(
            published_at__gte=window_start,
            published_at__lte=window_end,
        ).only(
            "id",
            "normalized_title",
            "matched_query",
        )

        candidate_entities = _entity_tokens(candidate)

        for article in recent_candidates:
            if not titles_are_similar(
                normalized_title,
                article.normalized_title,
                threshold=cls.NEAR_DUPLICATE_THRESHOLD,
            ):
                continue

            article_entities = {
                token
                for token in re.findall(
                    r"[a-z0-9]+",
                    " ".join(
                        (
                            article.matched_query or "",
                            article.normalized_title or "",
                        )
                    ).lower(),
                )
                if (
                    len(token) >= 3
                    and token not in _STOPWORDS
                    and token not in _EVENT_ENTITY_STOPWORDS
                )
            }
            # Fuzzy similarity alone is too broad for generic headlines such
            # as "First alert article" and "Second alert article". Require at
            # least one meaningful entity token in common before treating two
            # different headlines as the same event.
            if candidate_entities & article_entities:
                return article

        candidate_family = _event_family(candidate.title)
        if candidate_family and candidate_entities:
            for article in recent_candidates:
                if _event_family(article.normalized_title) != candidate_family:
                    continue
                article_entities = {
                    token
                    for token in re.findall(
                        r"[a-z0-9]+",
                        " ".join(
                            (
                                article.matched_query or "",
                                article.normalized_title or "",
                            )
                        ).lower(),
                    )
                    if (
                        len(token) >= 3
                        and token not in _STOPWORDS
                        and token not in _EVENT_ENTITY_STOPWORDS
                    )
                }
                if candidate_entities & article_entities:
                    return article

        return None