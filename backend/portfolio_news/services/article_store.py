import logging

from typing import Tuple

from django.db import IntegrityError

from .deduplication import (
    ArticleDeduplicator,
    compute_fingerprint,
    compute_url_hash,
)

from .news_provider import NewsArticleResult
from .source_quality import best_tier, classify_source
from .text_utils import (
    normalize_title,
    strip_html,
)


logger = logging.getLogger(__name__)


def _attach_source(article, candidate: NewsArticleResult) -> bool:
    """
    Attach `candidate` as a NewsArticleSource of `article`, if a
    source with the same URL isn't already attached.

    Returns True if a new NewsArticleSource row was created.
    Updates the article's denormalized source_quality/
    source_count so scoring never needs a join. Idempotent and
    safe under races via the DB uniqueness constraint.
    """

    from ..models import NewsArticleSource

    url_hash = compute_url_hash(candidate.url)
    tier = classify_source(candidate.source)

    try:
        _, source_created = NewsArticleSource.objects.get_or_create(
            article=article,
            url_hash=url_hash,
            defaults={
                "publisher_name": candidate.source[:200],
                "url": candidate.url[:1000],
                "quality_tier": tier,
                "published_at": candidate.published_at,
            },
        )
    except IntegrityError:
        # Lost a race with another process attaching the same
        # source concurrently - not an error, just already done.
        logger.debug(
            "Source already attached (race) article_id=%s url=%r",
            article.id,
            candidate.url,
        )
        return False

    if not source_created:
        return False

    existing_tiers = article.sources.values_list(
        "quality_tier", flat=True
    )

    article.source_quality = best_tier(existing_tiers)
    article.source_count = article.sources.count()
    article.save(update_fields=["source_quality", "source_count"])

    return True


def store_article(
    candidate: NewsArticleResult,
) -> Tuple["object", bool]:
    """
    Store a candidate article, deduplicating against what's
    already in the database.

    Returns (NewsArticle, created) - created=False means an
    equivalent article already existed. In that case the
    candidate's publisher is still recorded as an additional
    NewsArticleSource (unless it's a source already attached),
    so an event reported by several outlets retains all of them
    rather than silently discarding all but the first. Safe to
    call repeatedly with the same or overlapping candidates
    (idempotent).
    """

    from ..models import NewsArticle

    existing = ArticleDeduplicator.find_existing(candidate)

    if existing is not None:
        _attach_source(existing, candidate)
        return existing, False

    normalized_title = normalize_title(candidate.title)
    tier = classify_source(candidate.source)

    article = NewsArticle.objects.create(
        title=candidate.title[:500],
        normalized_title=normalized_title[:500],
        url=candidate.url[:1000],
        url_hash=compute_url_hash(candidate.url),
        source=candidate.source[:200],
        description=strip_html(candidate.description),
        published_at=candidate.published_at,
        fingerprint=compute_fingerprint(
            normalized_title,
            candidate.published_at,
        ),
        matched_query=candidate.matched_query[:255],
        source_quality=tier,
        source_count=1,
    )

    _attach_source(article, candidate)

    logger.debug(
        "Stored new article id=%s title=%r source_quality=%s",
        article.id,
        article.title,
        article.source_quality,
    )

    return article, True