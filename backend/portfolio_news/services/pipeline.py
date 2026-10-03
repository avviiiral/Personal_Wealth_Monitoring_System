import logging
import os

from datetime import timedelta
from typing import Optional

from django.contrib.auth.models import User
from django.utils import timezone

from users.permissions import get_active_family_group

from .article_store import store_article
from .rule_based_analyzer import RuleBasedArticleAnalyzer
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

# rule-based analyzer calls are now made in batches rather than once per article.
# Keep this at zero by default because batching already provides the
# request-rate reduction that the old per-article delay was intended to
# provide.
DEFAULT_ANALYSIS_DELAY_SECONDS = 0.0

# Cost control: even after the deterministic HoldingMatcher filter, a
# single holding can surface many candidate articles. This caps how many
# articles for a holding are selected for rule-based analysis in one monitoring
# run. Remaining candidates are deferred to the next run.
DEFAULT_MAX_ARTICLES_PER_HOLDING = 15

# Safety limit for analysis batch size. Multiple holdings are combined
# into batches, but a single request should not contain an unbounded
# number of articles.
DEFAULT_MAX_BATCH_ARTICLES = 50

# Deterministic floor below which an AI-judged relevance_score is treated
# as noise. The alert row is still created for idempotency but marked
# not relevant so it does not appear in the user's feed.
DEFAULT_MIN_RELEVANCE_SCORE = 30

# Same idea, but against the final composite alert_score.
DEFAULT_MIN_ALERT_SCORE = 2.0


def _get_analysis_delay_seconds() -> float:
    try:
        return float(
            os.environ.get(
                "NEWS_MONITOR_ANALYSIS_DELAY_SECONDS",
                DEFAULT_ANALYSIS_DELAY_SECONDS,
            )
        )
    except (TypeError, ValueError):
        return DEFAULT_ANALYSIS_DELAY_SECONDS


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
        "articles_analyzed": 0,
        "analysis_batch_requests": 0,
        "analysis_failures": 0,
        "alerts_created": 0,
        "notifications_sent": 0,
        "provider_failures": 0,
    }


def _process_holding(
    user,
    holding,
    provider: NewsProvider,
    analyzer: RuleBasedArticleAnalyzer,
    from_date,
    stats: dict,
    analysis_delay_seconds: float = 0.0,
    max_articles_per_holding: Optional[int] = None,
    min_relevance_score: Optional[int] = None,
    min_alert_score: Optional[float] = None,
    family=None,
) -> list:
    """
    Fetch, filter, store and select news articles for one holding.

    Rule-based analysis is intentionally NOT performed here anymore. The
    selected (article, holding) pairs are returned to the user-level
    pipeline so the deterministic analyzer can process them consistently.

    The analyzer and analysis threshold arguments remain in the
    signature for compatibility with existing callers/tests. analysis work
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
        # before any analysis work. This is the source for the raw
        # portfolio-news feed and therefore remains available even when
        # the local analyzer is unavailable or does not produce an alert.
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

        # The analysis cap controls local analysis only. We deliberately keep
        # storing every deterministic match discovered in this run so
        # the raw feed can show all matched articles without depending
        # on the local analyzer.
        if (
            resolved_max_articles > 0
            and articles_selected_this_holding >= resolved_max_articles
        ):
            continue

        selected_pairs.append((article, holding, connection))
        articles_selected_this_holding += 1
        stats["articles_analyzed"] += 1

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
    analyzer: RuleBasedArticleAnalyzer,
    max_batch_articles: int,
    stats: dict,
) -> dict:
    """
    Analyze all selected article/holding pairs for a user in bounded
    local rule-based batches.

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

        logger.info(
            "Running rule-based batch analysis for user_id=%s batch=%d-%d "
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
                "rule-based batch analyzer raised for user_id=%s "
                "batch_start=%s batch_size=%s",
                user.id,
                start,
                len(batch),
            )
            batch_results = {}

        stats["analysis_batch_requests"] += 1

        if not batch_results:
            stats["analysis_failures"] += len(batch)
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
            # Missing result means the local analyzer did not return a usable
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
    analyzer: Optional[RuleBasedArticleAnalyzer] = None,
    lookback_days: Optional[int] = None,
    analysis_delay_seconds: Optional[float] = None,
    max_articles_per_holding: Optional[int] = None,
    min_relevance_score: Optional[int] = None,
    min_alert_score: Optional[float] = None,
) -> dict:
    """
    Runs the full portfolio news monitoring pipeline for every active
    user: load holdings -> generate queries -> retrieve news ->
    deterministic relevance filter -> deduplicate -> select articles ->
    batch AI analysis -> portfolio-weighted alert creation.

    Rule-based analysis is performed in bounded batches across ALL holdings
    belonging to the same user. This avoids one the local analyzer request per
    article/holding pair and substantially reduces request-per-minute
    pressure.

    Safe to run repeatedly: deduplication and the
    (user, article, holding) uniqueness constraint mean re-runs never
    create duplicate alerts or duplicate articles.

    Every operational threshold is configurable via environment
    variable (falling back to a documented default when unset or
    invalid):

        NEWS_MONITOR_LOOKBACK_DAYS (default 3)
        NEWS_MONITOR_ANALYSIS_DELAY_SECONDS (default 0.0)
        NEWS_MONITOR_MAX_ARTICLES_PER_HOLDING (default 15)
        NEWS_MONITOR_MAX_BATCH_ARTICLES (default 50)
        NEWS_MONITOR_MIN_RELEVANCE_SCORE (default 30)
        NEWS_MONITOR_MIN_ALERT_SCORE (default 2.0)

    NEWS_MONITOR_INTERVAL, the delay between runs when using
    `monitor_portfolio_news --loop`, is read by the management command.
    """
    provider = provider or GoogleNewsRSSProvider()
    analyzer = analyzer or RuleBasedArticleAnalyzer()

    resolved_lookback_days = (
        lookback_days
        if lookback_days is not None
        else _get_lookback_days()
    )

    # Kept as a configurable argument for backwards compatibility.
    # Kept for compatibility with deployments that already set the old
    # delay; no paid service is contacted by the local analyzer.
    resolved_analysis_delay_seconds = (
        analysis_delay_seconds
        if analysis_delay_seconds is not None
        else _get_analysis_delay_seconds()
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

    logger.info(
        "Portfolio news monitoring started (lookback_days=%s, "
        "analysis_delay_seconds=%s, max_articles_per_holding=%s, "
        "max_batch_articles=%s, min_relevance_score=%s, "
        "min_alert_score=%s)",
        resolved_lookback_days,
        resolved_analysis_delay_seconds,
        resolved_max_articles_per_holding,
        resolved_max_batch_articles,
        resolved_min_relevance_score,
        resolved_min_alert_score,
    )

    users = User.objects.filter(is_active=True)

    for user in users:
        try:
            holdings = get_monitored_holdings(user)
        except Exception:
            logger.exception(
                "Failed to load holdings for user_id=%s", user.id
            )
            continue

        if not holdings:
            continue

        stats["users_processed"] += 1

        logger.info(
            "Processing user_id=%s with %d holdings",
            user.id,
            len(holdings),
        )

        family = get_active_family_group(user)

        # First collect articles across ALL holdings. the local analyzer is called
        # only after the entire user's deterministic filtering stage is
        # complete, allowing different holdings to share a request.
        user_article_holding_pairs = []

        for holding in holdings:
            stats["holdings_processed"] += 1

            holding_pairs = _process_holding(
                user,
                holding,
                provider,
                analyzer,
                from_date,
                stats,
                analysis_delay_seconds=0.0,
                max_articles_per_holding=resolved_max_articles_per_holding,
                min_relevance_score=resolved_min_relevance_score,
                min_alert_score=resolved_min_alert_score,
                family=family,
            )

            if holding_pairs:
                user_article_holding_pairs.extend(holding_pairs)

        if not user_article_holding_pairs:
            continue

        logger.info(
            "user_id=%s selected %d article/holding pairs for rule-based batch "
            "analysis",
            user.id,
            len(user_article_holding_pairs),
        )

        # Apply the old delay, if explicitly configured, once per batch
        # instead of once per article. The default is zero because the
        # batching itself is the request-rate optimization.
        if resolved_analysis_delay_seconds > 0:
            import time

            analyses = {}

            for start in range(
                0,
                len(user_article_holding_pairs),
                max(1, resolved_max_batch_articles),
            ):
                batch = user_article_holding_pairs[
                    start : start + max(1, resolved_max_batch_articles)
                ]

                time.sleep(resolved_analysis_delay_seconds)

                try:
                    if callable(getattr(analyzer, "analyze_batch", None)):
                        batch_results = analyzer.analyze_batch(
                            batch,
                            user=user,
                        )
                    else:
                        batch_results = {}
                        for article, holding in batch:
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
                    logger.exception(
                        "Rule-based analyzer raised for user_id=%s "
                        "batch_start=%s batch_size=%s",
                        user.id,
                        start,
                        len(batch),
                    )
                    batch_results = {}

                stats["analysis_batch_requests"] += 1

                if not batch_results:
                    stats["analysis_failures"] += len(batch)
                else:
                    analyses.update(batch_results)
        else:
            analyses = _analyze_batches_for_user(
                user,
                user_article_holding_pairs,
                analyzer,
                resolved_max_batch_articles,
                stats,
            )

        _create_alerts_from_analyses(
            user,
            user_article_holding_pairs,
            analyses,
            resolved_min_relevance_score,
            resolved_min_alert_score,
            stats,
        )

    logger.info("Portfolio news monitoring finished: %s", stats)

    return stats
