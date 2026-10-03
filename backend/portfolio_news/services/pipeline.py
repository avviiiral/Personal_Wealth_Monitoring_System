import logging
import os
import time

from datetime import timedelta
from typing import Optional

from django.contrib.auth.models import User
from django.utils import timezone

from users.permissions import get_active_family_group

from .article_store import store_article
from .gemini_analyzer import GeminiArticleAnalyzer
from .google_news_provider import GoogleNewsRSSProvider
from .holding_matcher import HoldingMatcher
from .holdings_registry import get_monitored_holdings
from .news_provider import NewsProvider
from .notification_creation import create_alert_from_analysis
from .web_push import deliver_alert_notification
from .query_builder import QueryBuilder
from ..models import PortfolioNewsMatch


logger = logging.getLogger(__name__)


DEFAULT_LOOKBACK_DAYS = 3

# Gemini calls are now made in batches rather than once per article.
# Keep this at zero by default because batching already provides the
# request-rate reduction that the old per-article delay was intended to
# provide.
DEFAULT_AI_CALL_DELAY_SECONDS = 0.0

# Cost control: even after the deterministic HoldingMatcher filter, a
# single holding can surface many candidate articles. This caps how many
# articles for a holding are selected for AI analysis in one monitoring
# run. Remaining candidates are deferred to the next run.
DEFAULT_MAX_ARTICLES_PER_HOLDING = 15

# Safety limit for Gemini request size. Multiple holdings are combined
# into batches, but a single request should not contain an unbounded
# number of articles.
DEFAULT_MAX_BATCH_ARTICLES = 50

# Deterministic floor below which an AI-judged relevance_score is treated
# as noise. The alert row is still created for idempotency but marked
# not relevant so it does not appear in the user's feed.
DEFAULT_MIN_RELEVANCE_SCORE = 30

# Same idea, but against the final composite alert_score.
DEFAULT_MIN_ALERT_SCORE = 2.0


# Reliability: the automatic scheduler and `monitor_portfolio_news --loop`
# must never spin in a hot loop because of a bad NEWS_MONITOR_INTERVAL
# value (0, negative or tiny). Anything below this floor is raised to it.
DEFAULT_MONITOR_INTERVAL_SECONDS = 1800
MIN_MONITOR_INTERVAL_SECONDS = 60

# Accuracy/speed: Google News `after:` filters by calendar day only, so
# an article published a little before the lookback boundary can still be
# returned. Anything older than the lookback window plus this grace period
# is skipped before matching, storing or any AI work.
STALE_ARTICLE_GRACE_DAYS = 1


def get_monitor_interval_seconds() -> int:
    """
    Seconds between automatic news runs (NEWS_MONITOR_INTERVAL).

    Invalid values fall back to the default; values below
    MIN_MONITOR_INTERVAL_SECONDS are raised to that floor so a
    misconfiguration cannot cause a busy loop against Google News.
    """
    try:
        value = int(
            os.environ.get(
                "NEWS_MONITOR_INTERVAL",
                DEFAULT_MONITOR_INTERVAL_SECONDS,
            )
        )
    except (TypeError, ValueError):
        return DEFAULT_MONITOR_INTERVAL_SECONDS

    if value < MIN_MONITOR_INTERVAL_SECONDS:
        logger.warning(
            "NEWS_MONITOR_INTERVAL=%s is below the %s second minimum; "
            "using %s seconds.",
            value,
            MIN_MONITOR_INTERVAL_SECONDS,
            MIN_MONITOR_INTERVAL_SECONDS,
        )
        return MIN_MONITOR_INTERVAL_SECONDS

    return value


def _provider_failure_count(provider) -> int:
    value = getattr(provider, "failed_queries", 0)
    return value if isinstance(value, int) else 0


class _RunQueryCache:
    """
    Per-run memoisation of NewsProvider.search().

    Family members frequently hold the same stocks, so the same search
    query is otherwise sent to Google News once per user. Within one
    monitoring run the answer cannot meaningfully change, so identical
    (query, from_date, to_date) searches are served from memory.

    Failures are never cached: exceptions propagate to the caller, and a
    search during which the provider's own `failed_queries` counter went
    up (providers such as GoogleNewsRSSProvider swallow failures and
    return an empty list) is not stored, so a later user in the same run
    can still retry. A genuinely empty result is cached like any other.
    """

    def __init__(self, provider, stats: dict):
        self._provider = provider
        self._stats = stats
        self._cache = {}

    def __getattr__(self, name):
        # Anything other than search() (e.g. failure counters) is read
        # straight from the wrapped provider.
        return getattr(self._provider, name)

    def search(self, query, from_date=None, to_date=None):
        key = (query, from_date, to_date)
        cached = self._cache.get(key)

        if cached is not None:
            self._stats["query_cache_hits"] += 1
            return list(cached)

        failures_before = _provider_failure_count(self._provider)

        results = self._provider.search(
            query,
            from_date=from_date,
            to_date=to_date,
        )

        if _provider_failure_count(self._provider) == failures_before:
            self._cache[key] = list(results)

        return results


def _is_stale(published_at, cutoff) -> bool:
    """True when an article is clearly older than the lookback window."""
    if published_at is None or cutoff is None:
        return False

    try:
        return published_at < cutoff
    except TypeError:
        # Naive vs aware datetime: treat as unknown rather than failing.
        return False


def _get_ai_call_delay_seconds() -> float:
    try:
        return float(
            os.environ.get(
                "NEWS_MONITOR_AI_CALL_DELAY_SECONDS",
                DEFAULT_AI_CALL_DELAY_SECONDS,
            )
        )
    except (TypeError, ValueError):
        return DEFAULT_AI_CALL_DELAY_SECONDS


def _get_lookback_days() -> int:
    try:
        return int(
            os.environ.get(
                "NEWS_MONITOR_LOOKBACK_DAYS",
                DEFAULT_LOOKBACK_DAYS,
            )
        )
    except (TypeError, ValueError):
        return DEFAULT_LOOKBACK_DAYS


def _get_max_articles_per_holding() -> int:
    try:
        return int(
            os.environ.get(
                "NEWS_MONITOR_MAX_ARTICLES_PER_HOLDING",
                DEFAULT_MAX_ARTICLES_PER_HOLDING,
            )
        )
    except (TypeError, ValueError):
        return DEFAULT_MAX_ARTICLES_PER_HOLDING


def _get_max_batch_articles() -> int:
    try:
        return int(
            os.environ.get(
                "NEWS_MONITOR_MAX_BATCH_ARTICLES",
                DEFAULT_MAX_BATCH_ARTICLES,
            )
        )
    except (TypeError, ValueError):
        return DEFAULT_MAX_BATCH_ARTICLES


def _get_min_relevance_score() -> int:
    try:
        return int(
            os.environ.get(
                "NEWS_MONITOR_MIN_RELEVANCE_SCORE",
                DEFAULT_MIN_RELEVANCE_SCORE,
            )
        )
    except (TypeError, ValueError):
        return DEFAULT_MIN_RELEVANCE_SCORE


def _get_min_alert_score() -> float:
    try:
        return float(
            os.environ.get(
                "NEWS_MONITOR_MIN_ALERT_SCORE",
                DEFAULT_MIN_ALERT_SCORE,
            )
        )
    except (TypeError, ValueError):
        return DEFAULT_MIN_ALERT_SCORE


def _empty_stats() -> dict:
    return {
        "users_processed": 0,
        "holdings_processed": 0,
        "queries_run": 0,
        "articles_retrieved": 0,
        "articles_matched": 0,
        "articles_stored_new": 0,
        "duplicates_skipped": 0,
        "articles_sent_to_ai": 0,
        "ai_batch_requests": 0,
        "ai_failures": 0,
        "alerts_created": 0,
        "notifications_sent": 0,
        "provider_failures": 0,
        "query_cache_hits": 0,
        "stale_skipped": 0,
        "holding_failures": 0,
        "user_failures": 0,
    }


def _process_holding(
    user,
    holding,
    provider: NewsProvider,
    analyzer: GeminiArticleAnalyzer,
    from_date,
    stats: dict,
    ai_call_delay_seconds: float = 0.0,
    max_articles_per_holding: Optional[int] = None,
    min_relevance_score: Optional[int] = None,
    min_alert_score: Optional[float] = None,
    family=None,
) -> list:
    """
    Fetch, filter, store and select news articles for one holding.

    Gemini analysis is intentionally NOT performed here anymore. The
    selected (article, holding) pairs are returned to the user-level
    pipeline so multiple holdings can be analyzed in the same Gemini
    request.

    The analyzer and AI-related threshold arguments remain in the
    signature for compatibility with existing callers/tests. AI work
    is performed centrally by run_portfolio_news_monitor().
    """
    from ..models import PortfolioNewsAlert, PortfolioNewsMatch

    resolved_max_articles = (
        max_articles_per_holding
        if max_articles_per_holding is not None
        else _get_max_articles_per_holding()
    )

    try:
        queries = QueryBuilder.build_queries(holding)
    except Exception:
        logger.exception(
            "Query generation failed for holding=%r (user_id=%s)",
            holding.display_name,
            user.id,
        )
        return []

    stats["queries_run"] += len(queries)

    candidates = []
    seen_urls = set()

    stale_cutoff = (
        from_date - timedelta(days=STALE_ARTICLE_GRACE_DAYS)
        if from_date is not None
        else None
    )

    for query in queries:
        try:
            results = provider.search(query, from_date=from_date)
        except Exception:
            stats["provider_failures"] += 1
            logger.warning(
                "Provider search raised for query=%r holding=%r: "
                "continuing with remaining queries",
                query,
                holding.display_name,
            )
            continue

        stats["articles_retrieved"] += len(results)

        for result in results:
            if result.url in seen_urls:
                continue

            seen_urls.add(result.url)

            if _is_stale(result.published_at, stale_cutoff):
                stats["stale_skipped"] += 1
                continue

            candidates.append(result)

    logger.info(
        "user_id=%s holding=%r queries=%d raw_articles=%d",
        user.id,
        holding.display_name,
        len(queries),
        len(candidates),
    )

    selected_pairs = []
    articles_selected_this_holding = 0

    for candidate in candidates:
        if not HoldingMatcher.is_relevant(
            candidate.title,
            candidate.description,
            holding,
            matched_query=candidate.matched_query,
        ):
            continue

        stats["articles_matched"] += 1

        try:
            article, created = store_article(candidate)
        except Exception:
            logger.exception(
                "Failed to store article url=%r for holding=%r",
                candidate.url,
                holding.display_name,
            )
            continue

        if created:
            stats["articles_stored_new"] += 1
        else:
            stats["duplicates_skipped"] += 1

        connection = HoldingMatcher.connection_for_article(
            candidate.title,
            candidate.description,
            holding,
            matched_query=candidate.matched_query,
        )

        # Persist the deterministic portfolio-to-article relationship
        # before any Gemini work. This is the source for the raw
        # portfolio-news feed and therefore remains available even when
        # Gemini is unavailable or does not produce an alert.
        PortfolioNewsMatch.objects.get_or_create(
            user=user,
            article=article,
            holding_type=holding.holding_type,
            holding_id=holding.holding_id,
            defaults={
                "holding_display_name": holding.display_name,
                "matched_query": candidate.matched_query[:255],
            },
        )

        # Never re-analyze an article already processed for this exact
        # (user, holding) pair, regardless of the previous relevance.
        already_processed = PortfolioNewsAlert.objects.filter(
            user=user,
            article=article,
            holding_type=holding.holding_type,
            holding_id=holding.holding_id,
        ).exists()

        if already_processed:
            continue

        # The AI cap controls Gemini usage only. We deliberately keep
        # storing every deterministic match discovered in this run so
        # the raw feed can show all matched articles without depending
        # on Gemini.
        if (
            resolved_max_articles > 0
            and articles_selected_this_holding >= resolved_max_articles
        ):
            continue

        selected_pairs.append((article, holding, connection))
        articles_selected_this_holding += 1
        stats["articles_sent_to_ai"] += 1

    return selected_pairs


def _analyze_one_pair_compatibly(
    analyzer,
    article,
    holding,
    user=None,
):
    """Use batch analysis when available, otherwise use the legacy API."""
    analyze_batch = getattr(analyzer, "analyze_batch", None)
    if callable(analyze_batch):
        results = analyze_batch([(article, holding)], user=user) or {}
        key = (article.id, holding.holding_type, holding.holding_id)
        return results.get(key)

    analyze = getattr(analyzer, "analyze", None)
    if callable(analyze):
        return analyze(article, holding, user=user)

    return None


def _analyze_batches_for_user(
    user,
    article_holding_pairs,
    analyzer: GeminiArticleAnalyzer,
    max_batch_articles: int,
    stats: dict,
    ai_call_delay_seconds: float = 0.0,
) -> dict:
    """
    Analyze all selected article/holding pairs for a user in bounded
    Gemini batches.

    A positive ai_call_delay_seconds is applied once before each batch
    request (the delay is a rate-limit courtesy, not a retry mechanism).

    Returns a mapping keyed by:
        (article_id, holding_type, holding_id)
    to ArticleAnalysis.
    """
    if not article_holding_pairs:
        return {}

    if max_batch_articles <= 0:
        max_batch_articles = DEFAULT_MAX_BATCH_ARTICLES

    analyses = {}

    for start in range(0, len(article_holding_pairs), max_batch_articles):
        batch = article_holding_pairs[
            start : start + max_batch_articles
        ]
        analyzer_batch = batch

        if ai_call_delay_seconds > 0:
            time.sleep(ai_call_delay_seconds)

        logger.info(
            "Running Gemini batch analysis for user_id=%s batch=%d-%d "
            "of %d article/holding pairs",
            user.id,
            start + 1,
            start + len(batch),
            len(article_holding_pairs),
        )

        try:
            if callable(getattr(analyzer, "analyze_batch", None)):
                batch_results = analyzer.analyze_batch(
                    analyzer_batch,
                    user=user,
                )
            else:
                batch_results = {}
                for article, holding, _ in batch:
                    analysis = _analyze_one_pair_compatibly(
                        analyzer,
                        article,
                        holding,
                        user=user,
                    )
                    if analysis is not None:
                        batch_results[
                            (
                                article.id,
                                holding.holding_type,
                                holding.holding_id,
                            )
                        ] = analysis
        except Exception:
            # Keep the whole monitoring run alive even if a custom
            # analyzer implementation unexpectedly raises.
            logger.exception(
                "Gemini batch analyzer raised for user_id=%s "
                "batch_start=%s batch_size=%s",
                user.id,
                start,
                len(batch),
            )
            batch_results = {}

        stats["ai_batch_requests"] += 1

        if not batch_results:
            stats["ai_failures"] += len(batch)
            continue

        analyses.update(batch_results)

    return analyses


def _create_alerts_from_analyses(
    user,
    article_holding_pairs,
    analyses,
    min_relevance_score: int,
    min_alert_score: float,
    stats: dict,
) -> None:
    """Create the existing PortfolioNewsAlert rows from batch results."""
    for article, holding, connection in article_holding_pairs:
        key = (
            article.id,
            holding.holding_type,
            holding.holding_id,
        )

        analysis = analyses.get(key)

        if analysis is None:
            # Missing result means Gemini did not return a usable
            # analysis for this pair. Do not manufacture an alert.
            continue

        if analysis.relevance_score < min_relevance_score:
            analysis.relevant = False

        try:
            alert, alert_created = create_alert_from_analysis(
                user,
                article,
                holding,
                analysis,
                connection=connection,
            )
        except Exception:
            logger.exception(
                "Failed to create alert for article id=%s holding=%r "
                "user_id=%s",
                article.id,
                holding.display_name,
                user.id,
            )
            continue

        if not alert_created:
            continue

        stats["alerts_created"] += 1

        # Apply the deterministic final alert-score floor exactly as
        # before. The alert row remains for idempotency but is hidden
        # from the feed and cannot trigger a notification.
        if (
            alert.relevant
            and alert.alert_score < min_alert_score
        ):
            alert.relevant = False
            alert.notification_sent = False
            alert.save(
                update_fields=[
                    "relevant",
                    "notification_sent",
                ]
            )

        if alert.relevant and deliver_alert_notification(alert):
            stats["notifications_sent"] += 1


def run_portfolio_news_monitor(
    provider: Optional[NewsProvider] = None,
    analyzer: Optional[GeminiArticleAnalyzer] = None,
    lookback_days: Optional[int] = None,
    ai_call_delay_seconds: Optional[float] = None,
    max_articles_per_holding: Optional[int] = None,
    min_relevance_score: Optional[int] = None,
    min_alert_score: Optional[float] = None,
) -> dict:
    """
    Runs the full portfolio news monitoring pipeline for every active
    user: load holdings -> generate queries -> retrieve news ->
    deterministic relevance filter -> deduplicate -> select articles ->
    batch AI analysis -> portfolio-weighted alert creation.

    Gemini analysis is performed in bounded batches across ALL holdings
    belonging to the same user. This avoids one Gemini request per
    article/holding pair and substantially reduces request-per-minute
    pressure.

    Safe to run repeatedly: deduplication and the
    (user, article, holding) uniqueness constraint mean re-runs never
    create duplicate alerts or duplicate articles.

    Failure isolation: a failure for one holding is logged and skipped,
    and a failure for one user is logged and skipped, so the remaining
    holdings/users are still processed (see the `holding_failures` and
    `user_failures` counters). Identical search queries are served from a
    per-run cache (`query_cache_hits`), and articles clearly older than the
    lookback window are skipped (`stale_skipped`).

    Every operational threshold is configurable via environment
    variable (falling back to a documented default when unset or
    invalid):

        NEWS_MONITOR_LOOKBACK_DAYS (default 3)
        NEWS_MONITOR_AI_CALL_DELAY_SECONDS (default 0.0)
        NEWS_MONITOR_MAX_ARTICLES_PER_HOLDING (default 15)
        NEWS_MONITOR_MAX_BATCH_ARTICLES (default 50)
        NEWS_MONITOR_MIN_RELEVANCE_SCORE (default 30)
        NEWS_MONITOR_MIN_ALERT_SCORE (default 2.0)

    NEWS_MONITOR_INTERVAL, the delay between runs when using
    `monitor_portfolio_news --loop`, is read by the management command.
    """
    provider = provider or GoogleNewsRSSProvider()
    analyzer = analyzer or GeminiArticleAnalyzer()

    resolved_lookback_days = (
        lookback_days
        if lookback_days is not None
        else _get_lookback_days()
    )

    # Kept as a configurable argument for backwards compatibility.
    # Batching means there is no per-article sleep anymore. A positive
    # value is applied once before each Gemini batch request.
    resolved_ai_call_delay_seconds = (
        ai_call_delay_seconds
        if ai_call_delay_seconds is not None
        else _get_ai_call_delay_seconds()
    )

    resolved_max_articles_per_holding = (
        max_articles_per_holding
        if max_articles_per_holding is not None
        else _get_max_articles_per_holding()
    )

    resolved_max_batch_articles = _get_max_batch_articles()

    resolved_min_relevance_score = (
        min_relevance_score
        if min_relevance_score is not None
        else _get_min_relevance_score()
    )

    resolved_min_alert_score = (
        min_alert_score
        if min_alert_score is not None
        else _get_min_alert_score()
    )

    from_date = timezone.now() - timedelta(
        days=resolved_lookback_days
    )

    stats = _empty_stats()

    # Identical searches (e.g. the same stock held by several family
    # members) are fetched once per run.
    raw_provider = provider
    provider = _RunQueryCache(raw_provider, stats)

    logger.info(
        "Portfolio news monitoring started (lookback_days=%s, "
        "ai_call_delay_seconds=%s, max_articles_per_holding=%s, "
        "max_batch_articles=%s, min_relevance_score=%s, "
        "min_alert_score=%s)",
        resolved_lookback_days,
        resolved_ai_call_delay_seconds,
        resolved_max_articles_per_holding,
        resolved_max_batch_articles,
        resolved_min_relevance_score,
        resolved_min_alert_score,
    )

    users = User.objects.filter(is_active=True)

    for user in users:
        try:
            _process_user(
                user,
                provider,
                analyzer,
                from_date,
                stats,
                ai_call_delay_seconds=resolved_ai_call_delay_seconds,
                max_articles_per_holding=resolved_max_articles_per_holding,
                max_batch_articles=resolved_max_batch_articles,
                min_relevance_score=resolved_min_relevance_score,
                min_alert_score=resolved_min_alert_score,
            )
        except Exception:
            # One user's failure must never stop the other users from
            # being monitored.
            stats["user_failures"] += 1
            logger.exception(
                "Portfolio news monitoring failed for user_id=%s; "
                "continuing with the next user",
                user.id,
            )

    # Providers such as GoogleNewsRSSProvider swallow request failures and
    # return an empty list, so fold their own failure counter into the
    # run statistics.
    stats["provider_failures"] += _provider_failure_count(raw_provider)

    logger.info("Portfolio news monitoring finished: %s", stats)

    return stats


def _process_user(
    user,
    provider,
    analyzer,
    from_date,
    stats: dict,
    *,
    ai_call_delay_seconds: float,
    max_articles_per_holding: int,
    max_batch_articles: int,
    min_relevance_score: int,
    min_alert_score: float,
) -> None:
    """Run the full news pipeline for a single user."""
    try:
        holdings = get_monitored_holdings(user)
    except Exception:
        logger.exception(
            "Failed to load holdings for user_id=%s", user.id
        )
        return

    if not holdings:
        return

    stats["users_processed"] += 1

    logger.info(
        "Processing user_id=%s with %d holdings",
        user.id,
        len(holdings),
    )

    family = get_active_family_group(user)

    # First collect articles across ALL holdings. Gemini is called
    # only after the entire user's deterministic filtering stage is
    # complete, allowing different holdings to share a request.
    user_article_holding_pairs = []

    for holding in holdings:
        stats["holdings_processed"] += 1

        try:
            holding_pairs = _process_holding(
                user,
                holding,
                provider,
                analyzer,
                from_date,
                stats,
                ai_call_delay_seconds=0.0,
                max_articles_per_holding=max_articles_per_holding,
                min_relevance_score=min_relevance_score,
                min_alert_score=min_alert_score,
                family=family,
            )
        except Exception:
            # One holding's failure must never stop the user's other
            # holdings from being monitored.
            stats["holding_failures"] += 1
            logger.exception(
                "Portfolio news processing failed for holding=%r "
                "(user_id=%s); continuing with the next holding",
                holding.display_name,
                user.id,
            )
            continue

        if holding_pairs:
            user_article_holding_pairs.extend(holding_pairs)

    if not user_article_holding_pairs:
        return

    logger.info(
        "user_id=%s selected %d article/holding pairs for Gemini batch "
        "analysis",
        user.id,
        len(user_article_holding_pairs),
    )

    analyses = _analyze_batches_for_user(
        user,
        user_article_holding_pairs,
        analyzer,
        max_batch_articles,
        stats,
        ai_call_delay_seconds=ai_call_delay_seconds,
    )

    _create_alerts_from_analyses(
        user,
        user_article_holding_pairs,
        analyses,
        min_relevance_score,
        min_alert_score,
        stats,
    )
