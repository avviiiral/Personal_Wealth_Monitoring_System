"""
Tests for the Portfolio News workflow hardening changes:

* reliability - provider retries/circuit breaker, per-holding and per-user
  failure isolation, scheduler isolation, interval validation, and the
  AI-call-delay path used with analyzers that only expose ``analyze``
* speed       - per-run query cache, stale-article skipping, cheaper
  near-duplicate title comparison
* accuracy    - short-ticker matching, defensive alert-score clamping
"""

from datetime import timedelta
from decimal import Decimal
from difflib import SequenceMatcher
from unittest.mock import MagicMock, patch

import requests
from django.contrib.auth.models import User
from django.test import SimpleTestCase, TestCase
from django.utils import timezone as dj_timezone

from investments.models import Asset, AssetCategory, Holding
from portfolio_news.constants import HoldingType
from portfolio_news.models import NewsArticle, PortfolioNewsAlert, PortfolioNewsMatch
from portfolio_news.services import portfolio_news_scheduler
from portfolio_news.services.alert_scoring import compute_alert_score
from portfolio_news.services.deduplication import titles_are_similar
from portfolio_news.services.gemini_analyzer import ArticleAnalysis
from portfolio_news.services.google_news_provider import GoogleNewsRSSProvider
from portfolio_news.services.holding_matcher import HoldingMatcher
from portfolio_news.services.holdings_registry import MonitoredHolding
from portfolio_news.services.news_provider import NewsArticleResult
from portfolio_news.services.pipeline import (
    DEFAULT_MONITOR_INTERVAL_SECONDS,
    MIN_MONITOR_INTERVAL_SECONDS,
    get_monitor_interval_seconds,
    run_portfolio_news_monitor,
)
from users.models import FamilyGroup

FEED_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>Google News</title>
<item>
  <title>Aurobindo Pharma receives USFDA approval - Reuters</title>
  <link>https://news.example.com/hardening-1</link>
  <pubDate>Mon, 24 Aug 2026 09:00:00 GMT</pubDate>
  <description>Aurobindo Pharma received USFDA approval.</description>
  <source url="https://reuters.com">Reuters</source>
</item></channel></rss>
"""


def _ok_response(content=FEED_XML):
    response = MagicMock()
    response.content = content
    response.raise_for_status = MagicMock()
    return response


def _http_error_response(status_code):
    response = MagicMock()
    error_response = MagicMock()
    error_response.status_code = status_code
    response.raise_for_status = MagicMock(
        side_effect=requests.exceptions.HTTPError(
            f"{status_code} error", response=error_response
        )
    )
    return response


# ---------------------------------------------------------------- provider


class GoogleNewsProviderReliabilityTests(SimpleTestCase):
    def setUp(self):
        self.provider = GoogleNewsRSSProvider()
        patcher = patch("portfolio_news.services.google_news_provider.time.sleep")
        self.sleep = patcher.start()
        self.addCleanup(patcher.stop)

    @patch("portfolio_news.services.google_news_provider.requests.get")
    def test_transient_connection_error_is_retried_then_succeeds(self, mock_get):
        mock_get.side_effect = [
            requests.exceptions.ConnectionError("blip"),
            _ok_response(),
        ]

        results = self.provider.search("Aurobindo Pharma")

        self.assertEqual(len(results), 1)
        self.assertEqual(mock_get.call_count, 2)
        self.assertEqual(self.provider.failed_queries, 0)
        self.sleep.assert_called_once_with(self.provider.RETRY_BACKOFF_SECONDS)

    @patch("portfolio_news.services.google_news_provider.requests.get")
    def test_http_503_is_retried(self, mock_get):
        mock_get.side_effect = [_http_error_response(503), _ok_response()]

        results = self.provider.search("Aurobindo Pharma")

        self.assertEqual(len(results), 1)
        self.assertEqual(mock_get.call_count, 2)

    @patch("portfolio_news.services.google_news_provider.requests.get")
    def test_http_404_is_not_retried(self, mock_get):
        mock_get.return_value = _http_error_response(404)

        results = self.provider.search("Aurobindo Pharma")

        self.assertEqual(results, [])
        self.assertEqual(mock_get.call_count, 1)
        self.assertEqual(self.provider.failed_queries, 1)

    @patch("portfolio_news.services.google_news_provider.requests.get")
    def test_gives_up_after_max_attempts_and_counts_failure(self, mock_get):
        mock_get.side_effect = requests.exceptions.Timeout("slow")

        results = self.provider.search("Aurobindo Pharma")

        self.assertEqual(results, [])
        self.assertEqual(mock_get.call_count, self.provider.REQUEST_ATTEMPTS)
        self.assertEqual(self.provider.failed_queries, 1)

    @patch("portfolio_news.services.google_news_provider.time.monotonic")
    @patch("portfolio_news.services.google_news_provider.requests.get")
    def test_circuit_breaker_opens_then_recovers_after_cooldown(
        self, mock_get, mock_monotonic
    ):
        clock = {"now": 1000.0}
        mock_monotonic.side_effect = lambda: clock["now"]
        mock_get.return_value = _http_error_response(404)

        for index in range(self.provider.CIRCUIT_BREAKER_THRESHOLD):
            self.provider.search(f"query {index}")

        calls_before = mock_get.call_count

        # Circuit is open: no further network calls, but still counted.
        self.assertEqual(self.provider.search("another query"), [])
        self.assertEqual(mock_get.call_count, calls_before)
        self.assertEqual(
            self.provider.failed_queries,
            self.provider.CIRCUIT_BREAKER_THRESHOLD + 1,
        )

        # After the cooldown the provider tries again.
        clock["now"] += self.provider.CIRCUIT_BREAKER_COOLDOWN_SECONDS + 1
        mock_get.return_value = _ok_response()
        self.assertEqual(len(self.provider.search("recovered query")), 1)
        self.assertEqual(mock_get.call_count, calls_before + 1)

    @patch("portfolio_news.services.google_news_provider.requests.get")
    def test_success_resets_consecutive_failure_count(self, mock_get):
        mock_get.return_value = _http_error_response(404)
        for index in range(self.provider.CIRCUIT_BREAKER_THRESHOLD - 1):
            self.provider.search(f"query {index}")

        mock_get.return_value = _ok_response()
        self.provider.search("good query")

        mock_get.return_value = _http_error_response(404)
        for index in range(self.provider.CIRCUIT_BREAKER_THRESHOLD - 1):
            self.provider.search(f"later query {index}")

        # Never reached the threshold consecutively, so still closed.
        self.assertFalse(self.provider._circuit_is_open())


# ---------------------------------------------------------------- interval


class MonitorIntervalTests(SimpleTestCase):
    def _interval(self, value):
        with patch.dict("os.environ", {"NEWS_MONITOR_INTERVAL": value}):
            return get_monitor_interval_seconds()

    def test_valid_value_is_used(self):
        self.assertEqual(self._interval("300"), 300)

    def test_invalid_value_falls_back_to_default(self):
        self.assertEqual(self._interval("abc"), DEFAULT_MONITOR_INTERVAL_SECONDS)

    def test_zero_negative_and_tiny_values_are_raised_to_minimum(self):
        for value in ("0", "-5", "1", str(MIN_MONITOR_INTERVAL_SECONDS - 1)):
            self.assertEqual(
                self._interval(value), MIN_MONITOR_INTERVAL_SECONDS, value
            )

    def test_unset_uses_default(self):
        with patch.dict("os.environ", {}, clear=False) as env:
            env.pop("NEWS_MONITOR_INTERVAL", None)
            self.assertEqual(
                get_monitor_interval_seconds(), DEFAULT_MONITOR_INTERVAL_SECONDS
            )


# --------------------------------------------------------------- scheduler


class _StopLoop(BaseException):
    """Escapes the scheduler's `except Exception` to end the endless loop."""


class SchedulerIsolationTests(SimpleTestCase):
    def _run_one_cycle(self, *, news_side_effect=None, filing_side_effect=None):
        sleeps = []

        def fake_sleep(seconds):
            sleeps.append(seconds)
            # 1st call is the start-up delay, 2nd is the end-of-cycle sleep.
            if len(sleeps) >= 2:
                raise _StopLoop()

        news = MagicMock(
            side_effect=news_side_effect,
            return_value={
                key: 0
                for key in (
                    "users_processed",
                    "holdings_processed",
                    "articles_matched",
                    "articles_stored_new",
                    "alerts_created",
                    "notifications_sent",
                    "provider_failures",
                    "query_cache_hits",
                    "holding_failures",
                    "user_failures",
                )
            },
        )
        filings = MagicMock(side_effect=filing_side_effect, return_value={"ok": 1})

        with patch.object(portfolio_news_scheduler, "run_portfolio_news_monitor", news), \
             patch.object(portfolio_news_scheduler, "ingest_exchange_filings", filings), \
             patch.object(portfolio_news_scheduler, "close_old_connections"), \
             patch.object(portfolio_news_scheduler.time, "sleep", fake_sleep), \
             patch.dict("os.environ", {"FILING_INTELLIGENCE_ENABLED": "true"}):
            with self.assertRaises(_StopLoop):
                portfolio_news_scheduler.PortfolioNewsScheduler._run()

        return news, filings, sleeps

    def test_news_failure_does_not_skip_filing_ingestion(self):
        news, filings, sleeps = self._run_one_cycle(
            news_side_effect=RuntimeError("google down")
        )

        self.assertEqual(news.call_count, 1)
        self.assertEqual(filings.call_count, 1)
        self.assertGreaterEqual(sleeps[-1], MIN_MONITOR_INTERVAL_SECONDS)

    def test_filing_failure_does_not_stop_the_loop(self):
        news, filings, sleeps = self._run_one_cycle(
            filing_side_effect=RuntimeError("exchange down")
        )

        self.assertEqual(news.call_count, 1)
        self.assertEqual(filings.call_count, 1)
        self.assertEqual(len(sleeps), 2)

    def test_filings_are_skipped_when_feature_flag_is_off(self):
        news = MagicMock(return_value={})
        filings = MagicMock()
        with patch.object(portfolio_news_scheduler, "ingest_exchange_filings", filings), \
             patch.dict("os.environ", {"FILING_INTELLIGENCE_ENABLED": "false"}):
            portfolio_news_scheduler.PortfolioNewsScheduler._run_filings_once()
        filings.assert_not_called()
        news.assert_not_called()


# ---------------------------------------------------------------- pipeline


class _FakeProvider:
    def __init__(self, results_by_query=None):
        self.results_by_query = results_by_query or {}
        self.calls = []

    def search(self, query, from_date=None, to_date=None):
        self.calls.append(query)
        return self.results_by_query.get(query, [])


class _AnalyzeOnlyAnalyzer:
    """Legacy analyzer exposing only ``analyze`` (no ``analyze_batch``)."""

    def __init__(self, analysis):
        self.analysis = analysis
        self.call_count = 0

    def analyze(self, article, holding, user=None):
        self.call_count += 1
        return self.analysis


class _PipelineBase(TestCase):
    def _make_user_with_holding(self, username, asset_name="Aurobindo Pharma Limited",
                                symbol="AUROPHARMA", isin="INE406A01037",
                                family=None, user=None):
        user = user or User.objects.create_user(username=username, password="pw")
        family = family or FamilyGroup.objects.create(name=f"{username} family")
        user.profile.family_groups.add(family)

        asset = Asset.objects.create(
            owner=user,
            family=family,
            name=asset_name,
            category=AssetCategory.STOCK,
            symbol=symbol,
            isin=isin,
            is_active=True,
        )
        Holding.objects.create(
            owner=user,
            family=family,
            asset=asset,
            quantity=Decimal("10"),
            average_cost=Decimal("100"),
            invested_value=Decimal("1000"),
            current_price=Decimal("150"),
            current_value=Decimal("1500"),
            unrealized_pnl=Decimal("500"),
        )
        return user, asset

    def _article(self, url="https://reuters.com/hardening-article-1", published_at=None,
                 title="Aurobindo Pharma receives USFDA approval",
                 query="Aurobindo Pharma Limited"):
        return NewsArticleResult(
            title=title,
            url=url,
            source="Reuters",
            description="Aurobindo Pharma received USFDA approval.",
            published_at=published_at or dj_timezone.now(),
            matched_query=query,
        )

    @staticmethod
    def _analysis():
        return ArticleAnalysis(
            relevant=True,
            relevance_score=94,
            sentiment="negative",
            impact="high",
            impact_score=88,
            category="REGULATORY",
            time_horizon="medium_term",
            summary="Short factual summary.",
            portfolio_implication="Potential negative impact.",
            reason="Regulatory development.",
            confidence=0.91,
        )


class PipelineSpeedTests(_PipelineBase):
    def test_identical_queries_across_users_are_fetched_once_per_run(self):
        self._make_user_with_holding("alice")
        self._make_user_with_holding("bob")

        provider = _FakeProvider(
            {"Aurobindo Pharma Limited": [self._article()]}
        )
        analyzer = _AnalyzeOnlyAnalyzer(self._analysis())

        stats = run_portfolio_news_monitor(provider=provider, analyzer=analyzer)

        self.assertEqual(stats["users_processed"], 2)
        # Each distinct query hits the provider exactly once, not once
        # per user.
        self.assertEqual(len(provider.calls), len(set(provider.calls)))
        self.assertGreater(stats["query_cache_hits"], 0)
        # Both users still get their own match and alert.
        self.assertEqual(PortfolioNewsMatch.objects.count(), 2)
        self.assertEqual(PortfolioNewsAlert.objects.count(), 2)
        self.assertEqual(NewsArticle.objects.count(), 1)

    def test_cache_is_not_shared_between_runs(self):
        self._make_user_with_holding("alice")
        provider = _FakeProvider({"Aurobindo Pharma Limited": [self._article()]})
        analyzer = _AnalyzeOnlyAnalyzer(self._analysis())

        run_portfolio_news_monitor(provider=provider, analyzer=analyzer)
        first_run_calls = len(provider.calls)
        run_portfolio_news_monitor(provider=provider, analyzer=analyzer)

        self.assertEqual(len(provider.calls), first_run_calls * 2)

    def test_failed_searches_are_not_cached(self):
        # A provider that swallows failures returns [] and bumps its
        # failure counter; that must stay retryable for the next user.
        self._make_user_with_holding("alice")
        self._make_user_with_holding("bob")

        class _FailingProvider(_FakeProvider):
            failed_queries = 0

            def search(self, query, from_date=None, to_date=None):
                self.failed_queries += 1
                return super().search(query, from_date, to_date)

        provider = _FailingProvider()

        stats = run_portfolio_news_monitor(
            provider=provider, analyzer=_AnalyzeOnlyAnalyzer(self._analysis())
        )

        self.assertEqual(stats["query_cache_hits"], 0)
        self.assertEqual(len(provider.calls), 2 * len(set(provider.calls)))

    def test_genuinely_empty_results_are_cached(self):
        self._make_user_with_holding("alice")
        self._make_user_with_holding("bob")
        provider = _FakeProvider()  # always returns [] without failing

        stats = run_portfolio_news_monitor(
            provider=provider, analyzer=_AnalyzeOnlyAnalyzer(self._analysis())
        )

        self.assertEqual(len(provider.calls), len(set(provider.calls)))
        self.assertEqual(stats["query_cache_hits"], len(set(provider.calls)))

    def test_articles_older_than_lookback_window_are_skipped(self):
        self._make_user_with_holding("alice")
        old_article = self._article(
            url="https://reuters.com/hardening-old",
            published_at=dj_timezone.now() - timedelta(days=10),
        )
        provider = _FakeProvider({"Aurobindo Pharma Limited": [old_article]})
        analyzer = _AnalyzeOnlyAnalyzer(self._analysis())

        stats = run_portfolio_news_monitor(
            provider=provider, analyzer=analyzer, lookback_days=3
        )

        self.assertEqual(stats["stale_skipped"], 1)
        self.assertEqual(stats["articles_matched"], 0)
        self.assertEqual(NewsArticle.objects.count(), 0)
        self.assertEqual(analyzer.call_count, 0)

    def test_article_inside_grace_period_is_kept(self):
        self._make_user_with_holding("alice")
        # 3-day lookback + 1 day grace: 3.5 days old is still accepted.
        article = self._article(
            published_at=dj_timezone.now() - timedelta(days=3, hours=12)
        )
        provider = _FakeProvider({"Aurobindo Pharma Limited": [article]})

        stats = run_portfolio_news_monitor(
            provider=provider,
            analyzer=_AnalyzeOnlyAnalyzer(self._analysis()),
            lookback_days=3,
        )

        self.assertEqual(stats["stale_skipped"], 0)
        self.assertEqual(stats["articles_matched"], 1)

    def test_article_without_publish_date_is_kept(self):
        self._make_user_with_holding("alice")
        article = self._article()
        article.published_at = None
        provider = _FakeProvider({"Aurobindo Pharma Limited": [article]})

        stats = run_portfolio_news_monitor(
            provider=provider,
            analyzer=_AnalyzeOnlyAnalyzer(self._analysis()),
        )

        self.assertEqual(stats["stale_skipped"], 0)
        self.assertEqual(stats["articles_matched"], 1)

    def test_provider_failure_counter_is_reported(self):
        self._make_user_with_holding("alice")

        class _CountingProvider(_FakeProvider):
            failed_queries = 4

        stats = run_portfolio_news_monitor(
            provider=_CountingProvider(),
            analyzer=_AnalyzeOnlyAnalyzer(self._analysis()),
        )

        self.assertEqual(stats["provider_failures"], 4)


class PipelineReliabilityTests(_PipelineBase):
    def test_one_failing_holding_does_not_stop_the_others(self):
        user, _ = self._make_user_with_holding("alice")
        self._make_user_with_holding(
            "alice",
            asset_name="Sun Pharma Limited",
            symbol="SUNPHARMA",
            isin="INE044A01036",
            user=user,
            family=user.profile.family_groups.first(),
        )

        provider = _FakeProvider({"Aurobindo Pharma Limited": [self._article()]})
        analyzer = _AnalyzeOnlyAnalyzer(self._analysis())

        original = HoldingMatcher.is_relevant

        def flaky(title, description, holding, matched_query=""):
            if holding.display_name == "Sun Pharma Limited":
                raise RuntimeError("boom")
            return original(title, description, holding, matched_query)

        # Make Sun Pharma return something so is_relevant is reached.
        provider.results_by_query["Sun Pharma Limited"] = [
            self._article(
                url="https://reuters.com/hardening-sun",
                title="Sun Pharma Limited gets approval",
                query="Sun Pharma Limited",
            )
        ]

        with patch(
            "portfolio_news.services.pipeline.HoldingMatcher.is_relevant",
            side_effect=flaky,
        ):
            stats = run_portfolio_news_monitor(provider=provider, analyzer=analyzer)

        self.assertEqual(stats["holding_failures"], 1)
        self.assertEqual(stats["holdings_processed"], 2)
        self.assertEqual(stats["alerts_created"], 1)

    def test_one_failing_user_does_not_stop_other_users(self):
        alice, _ = self._make_user_with_holding("alice")
        self._make_user_with_holding("bob")

        provider = _FakeProvider({"Aurobindo Pharma Limited": [self._article()]})
        analyzer = _AnalyzeOnlyAnalyzer(self._analysis())

        from portfolio_news.services import pipeline

        real = pipeline.get_active_family_group

        def flaky(user):
            if user.pk == alice.pk:
                raise RuntimeError("family lookup failed")
            return real(user)

        with patch.object(pipeline, "get_active_family_group", side_effect=flaky):
            stats = run_portfolio_news_monitor(provider=provider, analyzer=analyzer)

        self.assertEqual(stats["user_failures"], 1)
        self.assertEqual(stats["alerts_created"], 1)
        self.assertEqual(PortfolioNewsAlert.objects.get().user.username, "bob")

    def test_ai_call_delay_works_with_analyzer_that_has_no_batch_method(self):
        # Regression: the delay path used to unpack (article, holding)
        # from (article, holding, connection) tuples, so every batch
        # failed and no alert was ever created when a delay was set.
        self._make_user_with_holding("alice")
        provider = _FakeProvider({"Aurobindo Pharma Limited": [self._article()]})
        analyzer = _AnalyzeOnlyAnalyzer(self._analysis())

        with patch("portfolio_news.services.pipeline.time.sleep") as sleep:
            stats = run_portfolio_news_monitor(
                provider=provider,
                analyzer=analyzer,
                ai_call_delay_seconds=0.5,
            )

        sleep.assert_called_once_with(0.5)
        self.assertEqual(stats["ai_failures"], 0)
        self.assertEqual(stats["alerts_created"], 1)
        self.assertEqual(analyzer.call_count, 1)


# ----------------------------------------------------------------- accuracy


class ShortTickerMatchingTests(SimpleTestCase):
    def setUp(self):
        self.holding = MonitoredHolding(
            holding_type=HoldingType.EQUITY,
            holding_id=1,
            display_name="Tata Consultancy Services Limited",
            aliases=[],
            symbol="TCS",
            isin="INE467B01029",
        )

    def test_short_ticker_in_capitals_matches(self):
        self.assertTrue(
            HoldingMatcher.is_relevant("TCS shares gain on deal win", "", self.holding)
        )

    def test_short_ticker_as_ordinary_lowercase_word_does_not_match(self):
        short = MonitoredHolding(
            holding_type=HoldingType.EQUITY,
            holding_id=2,
            display_name="Information Technology Holdings Ltd",
            aliases=[],
            symbol="IT",
            isin="",
        )
        self.assertFalse(
            HoldingMatcher.is_relevant(
                "Why it matters for investors",
                "Analysts said it could rally further.",
                short,
            )
        )

    def test_short_ticker_lowercase_variant_of_real_word_is_rejected(self):
        self.assertFalse(
            HoldingMatcher.is_relevant("Why tcs is not mentioned here", "", self.holding)
        )

    def test_long_ticker_stays_case_insensitive(self):
        holding = MonitoredHolding(
            holding_type=HoldingType.EQUITY,
            holding_id=3,
            display_name="Aurobindo Pharma Limited",
            aliases=[],
            symbol="AUROPHARMA",
            isin="",
        )
        self.assertTrue(
            HoldingMatcher.is_relevant("auropharma shares rally", "", holding)
        )


class TitleSimilarityParityTests(SimpleTestCase):
    PAIRS = [
        ("aurobindo pharma receives usfda approval", "aurobindo pharma gets usfda nod"),
        ("aurobindo pharma receives usfda approval", "kalyan jewellers opens new showroom"),
        ("tcs wins major deal", "tcs wins major deal"),
        ("short", "a considerably longer unrelated headline about markets"),
        ("rbi raises repo rate by 25 bps", "rbi hikes repo rate by 25 basis points"),
    ]

    def test_results_match_plain_sequence_matcher_ratio(self):
        for threshold in (0.5, 0.72, 0.9):
            for a, b in self.PAIRS:
                expected = SequenceMatcher(None, a, b).ratio() >= threshold
                self.assertEqual(
                    titles_are_similar(a, b, threshold=threshold),
                    expected,
                    (a, b, threshold),
                )

    def test_empty_titles_are_never_similar(self):
        self.assertFalse(titles_are_similar("", "anything"))
        self.assertFalse(titles_are_similar("anything", ""))


class AlertScoreClampingTests(SimpleTestCase):
    def test_out_of_range_confidence_is_capped_at_one(self):
        self.assertEqual(
            compute_alert_score(80, 25.0, 5.0),
            compute_alert_score(80, 25.0, 1.0),
        )

    def test_negative_confidence_scores_zero(self):
        self.assertEqual(compute_alert_score(80, 25.0, -0.5), 0.0)

    def test_out_of_range_impact_is_capped_at_100(self):
        self.assertEqual(
            compute_alert_score(500, 50.0, 1.0),
            compute_alert_score(100, 50.0, 1.0),
        )

    def test_in_range_scores_are_unchanged(self):
        # Spec example: 80 x 25% x 1.0 = 20.0
        self.assertEqual(compute_alert_score(80, 25.0, 1.0), 20.0)
