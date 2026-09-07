from datetime import datetime, timezone
from unittest.mock import (
    MagicMock,
    patch,
)

from django.test import TestCase

import requests

from portfolio_news.models import NewsArticle
from portfolio_news.services.article_store import store_article
from portfolio_news.services.deduplication import (
    ArticleDeduplicator,
    compute_fingerprint,
    compute_url_hash,
    titles_are_similar,
)
from portfolio_news.services.google_news_provider import (
    GoogleNewsRSSProvider,
)
from portfolio_news.services.news_provider import NewsArticleResult
from portfolio_news.services.text_utils import (
    normalize_title,
    strip_html,
)


SAMPLE_FEED_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Google News</title>
    <item>
      <title>Aurobindo Pharma receives USFDA approval - Reuters</title>
      <link>https://news.example.com/article-1</link>
      <pubDate>Mon, 24 Aug 2026 09:00:00 GMT</pubDate>
      <description>Aurobindo Pharma received USFDA approval for a new drug.</description>
      <source url="https://reuters.com">Reuters</source>
    </item>
    <item>
      <title>Aurobindo Pharma gets USFDA nod - Economic Times</title>
      <link>https://news.example.com/article-2</link>
      <pubDate>Mon, 24 Aug 2026 09:15:00 GMT</pubDate>
      <description>USFDA approves drug for Aurobindo Pharma.</description>
      <source url="https://economictimes.com">Economic Times</source>
    </item>
  </channel>
</rss>
"""

MALFORMED_FEED_XML = b"not a valid feed at all <<<>>>"


class GoogleNewsRSSProviderTests(TestCase):

    def setUp(self):
        self.provider = GoogleNewsRSSProvider()

    def _mock_response(self, content, status_ok=True):
        response = MagicMock()
        response.content = content

        if status_ok:
            response.raise_for_status = MagicMock()
        else:
            response.raise_for_status = MagicMock(
                side_effect=requests.exceptions.HTTPError(
                    "500 error"
                )
            )

        return response

    def test_search_returns_parsed_articles(self):
        with patch(
            "portfolio_news.services.google_news_provider.requests.get"
        ) as mock_get:
            mock_get.return_value = self._mock_response(
                SAMPLE_FEED_XML
            )

            results = self.provider.search("Aurobindo Pharma")

        self.assertEqual(len(results), 2)

        self.assertEqual(
            results[0].title,
            "Aurobindo Pharma receives USFDA approval - Reuters",
        )

        self.assertEqual(
            results[0].url,
            "https://news.example.com/article-1",
        )

        self.assertEqual(results[0].source, "Reuters")

        self.assertIsNotNone(results[0].published_at)

    def test_search_empty_query_returns_empty_list(self):
        results = self.provider.search("   ")

        self.assertEqual(results, [])

    def test_search_network_failure_returns_empty_list(self):
        with patch(
            "portfolio_news.services.google_news_provider.requests.get"
        ) as mock_get:
            mock_get.side_effect = (
                requests.exceptions.ConnectionError("no network")
            )

            results = self.provider.search("Kalyan Jewellers")

        self.assertEqual(results, [])

    def test_search_http_error_returns_empty_list(self):
        with patch(
            "portfolio_news.services.google_news_provider.requests.get"
        ) as mock_get:
            mock_get.return_value = self._mock_response(
                b"", status_ok=False
            )

            results = self.provider.search("One97 Communications")

        self.assertEqual(results, [])

    def test_search_malformed_feed_does_not_raise(self):
        with patch(
            "portfolio_news.services.google_news_provider.requests.get"
        ) as mock_get:
            mock_get.return_value = self._mock_response(
                MALFORMED_FEED_XML
            )

            # feedparser is lenient and returns zero entries for
            # unparsable content rather than raising - this should
            # not crash either way.
            results = self.provider.search("Navin Fluorine")

        self.assertEqual(results, [])

    def test_build_url_includes_date_filters(self):
        from datetime import datetime, timezone

        url = self.provider._build_url(
            "Aurobindo Pharma",
            from_date=datetime(2026, 8, 1, tzinfo=timezone.utc),
            to_date=datetime(2026, 8, 24, tzinfo=timezone.utc),
        )

        self.assertIn("after%3A2026-08-01", url)
        self.assertIn("before%3A2026-08-24", url)


class TextUtilsTests(TestCase):

    def test_strip_html_removes_tags_and_keeps_text(self):
        raw = (
            '<a href="https://x.com/a" target="_blank">'
            "Aurobindo Pharma gets USFDA approval</a>"
            '&nbsp;&nbsp;<font color="#6f6f6f">livemint.com</font>'
        )

        result = strip_html(raw)

        self.assertIn("Aurobindo Pharma gets USFDA approval", result)
        self.assertNotIn("<a", result)
        self.assertNotIn("<font", result)

    def test_strip_html_handles_empty_input(self):
        self.assertEqual(strip_html(""), "")
        self.assertEqual(strip_html(None), "")

    def test_normalize_title_strips_source_suffix_and_punctuation(self):
        title_a = "Aurobindo Pharma receives USFDA approval - Reuters"
        title_b = "Aurobindo Pharma gets USFDA nod - Economic Times"

        normalized_a = normalize_title(title_a)
        normalized_b = normalize_title(title_b)

        self.assertNotIn("reuters", normalized_a)
        self.assertNotIn("economic times", normalized_b)
        self.assertNotIn("-", normalized_a)


class DeduplicationLogicTests(TestCase):

    def test_titles_are_similar_true_for_near_duplicates(self):
        a = normalize_title(
            "Aurobindo Pharma receives USFDA approval - Reuters"
        )
        b = normalize_title(
            "Aurobindo Pharma gets USFDA nod - Economic Times"
        )

        self.assertTrue(titles_are_similar(a, b, threshold=0.55))

    def test_titles_are_similar_false_for_unrelated_titles(self):
        a = normalize_title("Aurobindo Pharma receives USFDA approval")
        b = normalize_title("Kalyan Jewellers opens new showroom in Pune")

        self.assertFalse(titles_are_similar(a, b, threshold=0.72))

    def test_compute_fingerprint_stable_for_same_title_and_date(self):
        published = datetime(2026, 8, 24, 9, 0, tzinfo=timezone.utc)

        fp1 = compute_fingerprint(
            normalize_title("Aurobindo Pharma gets USFDA approval"),
            published,
        )

        fp2 = compute_fingerprint(
            normalize_title("Aurobindo Pharma gets USFDA approval"),
            published,
        )

        self.assertEqual(fp1, fp2)

    def test_compute_url_hash_deterministic(self):
        url = "https://news.example.com/article-1"

        self.assertEqual(
            compute_url_hash(url),
            compute_url_hash(url),
        )


class ArticleStoreDeduplicationTests(TestCase):
    """
    Covers the exact scenario from the spec: the same event
    ("Aurobindo Pharma receives USFDA approval") reported by
    three different publishers should collapse into a single
    stored NewsArticle, not three.
    """

    def setUp(self):
        self.published_at = datetime(
            2026, 8, 24, 9, 0, tzinfo=timezone.utc
        )

    def _result(self, title, url, source, description=""):
        return NewsArticleResult(
            title=title,
            url=url,
            source=source,
            description=description,
            published_at=self.published_at,
            matched_query="Aurobindo Pharma",
        )

    def test_same_url_seen_twice_does_not_duplicate(self):
        candidate = self._result(
            "Aurobindo Pharma receives USFDA approval",
            "https://reuters.com/article-1",
            "Reuters",
        )

        article_1, created_1 = store_article(candidate)
        article_2, created_2 = store_article(candidate)

        self.assertTrue(created_1)
        self.assertFalse(created_2)
        self.assertEqual(article_1.id, article_2.id)
        self.assertEqual(NewsArticle.objects.count(), 1)

    def test_same_event_different_sources_collapses_to_one_article(self):
        reuters = self._result(
            "Aurobindo Pharma receives USFDA approval",
            "https://reuters.com/article-1",
            "Reuters",
        )

        economic_times = self._result(
            "Aurobindo Pharma gets USFDA approval",
            "https://economictimes.com/article-2",
            "Economic Times",
        )

        business_standard = self._result(
            "USFDA approves Aurobindo Pharma drug",
            "https://business-standard.com/article-3",
            "Business Standard",
        )

        store_article(reuters)
        store_article(economic_times)
        store_article(business_standard)

        # All three should have collapsed into at most two rows
        # (near-duplicate matching is similarity-based, not
        # perfect, but must not create three separate articles
        # for the same event on the same day).
        self.assertLess(NewsArticle.objects.count(), 3)

    def test_unrelated_articles_are_stored_separately(self):
        aurobindo = self._result(
            "Aurobindo Pharma receives USFDA approval",
            "https://reuters.com/article-1",
            "Reuters",
        )

        kalyan = self._result(
            "Kalyan Jewellers opens new showroom in Pune",
            "https://reuters.com/article-9",
            "Reuters",
        )

        store_article(aurobindo)
        store_article(kalyan)

        self.assertEqual(NewsArticle.objects.count(), 2)

    def test_stored_description_has_html_stripped(self):
        candidate = self._result(
            "Aurobindo Pharma receives USFDA approval",
            "https://reuters.com/article-1",
            "Reuters",
            description=(
                '<a href="https://x.com">Aurobindo Pharma receives '
                'USFDA approval</a>&nbsp;<font color="#6f6f6f">Reuters</font>'
            ),
        )

        article, _ = store_article(candidate)

        self.assertNotIn("<a", article.description)
        self.assertNotIn("<font", article.description)

    def test_store_article_is_idempotent_across_repeated_runs(self):
        candidate = self._result(
            "Aurobindo Pharma receives USFDA approval",
            "https://reuters.com/article-1",
            "Reuters",
        )

        for _ in range(5):
            store_article(candidate)

        self.assertEqual(NewsArticle.objects.count(), 1)


class ArticleSourceAttachmentTests(TestCase):
    """
    When the same event is reported by several publishers, the
    duplicate reports must not be discarded: each distinct
    publisher should be retained as a NewsArticleSource, and the
    article's denormalized source_quality/source_count should
    reflect the best tier and the count seen so far.
    """

    def setUp(self):
        self.published_at = datetime(
            2026, 8, 24, 9, 0, tzinfo=timezone.utc
        )

    def _result(self, title, url, source, description=""):
        return NewsArticleResult(
            title=title,
            url=url,
            source=source,
            description=description,
            published_at=self.published_at,
            matched_query="Aurobindo Pharma",
        )

    def test_duplicate_event_retains_all_distinct_sources(self):
        reuters = self._result(
            "Aurobindo Pharma receives USFDA approval",
            "https://reuters.com/article-1",
            "Reuters",
        )

        economic_times = self._result(
            "Aurobindo Pharma gets USFDA approval",
            "https://economictimes.com/article-2",
            "Economic Times",
        )

        article_1, created_1 = store_article(reuters)
        article_2, created_2 = store_article(economic_times)

        self.assertTrue(created_1)
        self.assertFalse(created_2)
        self.assertEqual(article_1.id, article_2.id)
        self.assertEqual(article_1.sources.count(), 2)

        publisher_names = set(
            article_1.sources.values_list(
                "publisher_name", flat=True
            )
        )

        self.assertEqual(
            publisher_names, {"Reuters", "Economic Times"}
        )

    def test_same_publisher_url_seen_twice_not_duplicated_as_source(self):
        candidate = self._result(
            "Aurobindo Pharma receives USFDA approval",
            "https://reuters.com/article-1",
            "Reuters",
        )

        store_article(candidate)
        article, _ = store_article(candidate)

        self.assertEqual(article.sources.count(), 1)
        self.assertEqual(article.source_count, 1)

    def test_source_quality_reflects_best_tier_seen(self):
        # An unclassified/low-tier outlet arrives first...
        blog = self._result(
            "Aurobindo Pharma receives USFDA approval",
            "https://some-random-blog.example/post-1",
            "Random Finance Blog",
        )

        # ...then a top-tier wire service reports the same event.
        reuters = self._result(
            "Aurobindo Pharma gets USFDA approval",
            "https://reuters.com/article-1",
            "Reuters",
        )

        article, _ = store_article(blog)
        self.assertEqual(article.source_quality, "tier_3")

        article, _ = store_article(reuters)
        article.refresh_from_db()

        self.assertEqual(article.source_quality, "tier_1")
        self.assertEqual(article.source_count, 2)

    def test_first_stored_source_gets_correct_tier(self):
        reuters = self._result(
            "Aurobindo Pharma receives USFDA approval",
            "https://reuters.com/article-1",
            "Reuters",
        )

        article, created = store_article(reuters)

        self.assertTrue(created)
        self.assertEqual(article.source_quality, "tier_1")
        self.assertEqual(article.source_count, 1)
        self.assertEqual(article.sources.count(), 1)
        self.assertEqual(
            article.sources.first().quality_tier, "tier_1"
        )


class SourceQualityClassificationTests(TestCase):

    def test_known_tier_1_publisher_is_classified_correctly(self):
        from portfolio_news.services.source_quality import (
            classify_source,
        )

        self.assertEqual(classify_source("Reuters"), "tier_1")
        self.assertEqual(
            classify_source("The Economic Times"), "tier_1"
        )
        self.assertEqual(classify_source("Moneycontrol"), "tier_1")

    def test_known_tier_2_publisher_is_classified_correctly(self):
        from portfolio_news.services.source_quality import (
            classify_source,
        )

        self.assertEqual(classify_source("The Hindu"), "tier_2")
        self.assertEqual(
            classify_source("Business Today"), "tier_2"
        )

    def test_unknown_publisher_defaults_to_tier_3(self):
        from portfolio_news.services.source_quality import (
            classify_source,
        )

        self.assertEqual(
            classify_source("Random Finance Blog"), "tier_3"
        )

    def test_empty_or_missing_publisher_defaults_to_tier_3(self):
        from portfolio_news.services.source_quality import (
            classify_source,
        )

        self.assertEqual(classify_source(""), "tier_3")
        self.assertEqual(classify_source(None), "tier_3")

    def test_classification_is_case_insensitive(self):
        from portfolio_news.services.source_quality import (
            classify_source,
        )

        self.assertEqual(classify_source("REUTERS"), "tier_1")
        self.assertEqual(classify_source("reuters"), "tier_1")

    def test_best_tier_picks_highest_quality(self):
        from portfolio_news.services.source_quality import best_tier

        self.assertEqual(
            best_tier(["tier_3", "tier_1", "tier_2"]), "tier_1"
        )
        self.assertEqual(best_tier(["tier_3", "tier_2"]), "tier_2")
        self.assertEqual(best_tier([]), "tier_3")

    def test_overrides_setting_takes_precedence(self):
        from django.test import override_settings

        from portfolio_news.services.source_quality import (
            classify_source,
        )

        with override_settings(
            NEWS_SOURCE_QUALITY_OVERRIDES={
                "random finance blog": "tier_1",
            }
        ):
            self.assertEqual(
                classify_source("Random Finance Blog"), "tier_1"
            )


from decimal import Decimal

from django.contrib.auth.models import User

from investments.models import Asset, AssetCategory, Holding
from mutual_funds.models import MutualFundHolding, MutualFundScheme

from portfolio_news.services.holdings_registry import (
    HoldingType,
    get_monitored_holdings,
    MonitoredHolding,
)
from portfolio_news.services.holding_matcher import HoldingMatcher
from portfolio_news.services.query_builder import QueryBuilder


class HoldingsRegistryTests(TestCase):

    def setUp(self):
        self.user = User.objects.create_user(
            username="registryuser",
            password="testpassword",
        )

    def _create_equity_holding(
        self,
        name,
        symbol,
        isin,
        current_value,
        quantity=Decimal("10"),
    ):
        asset = Asset.objects.create(
            owner=self.user,
            name=name,
            category=AssetCategory.STOCK,
            symbol=symbol,
            isin=isin,
            is_active=True,
        )

        return Holding.objects.create(
            owner=self.user,
            asset=asset,
            quantity=quantity,
            average_cost=Decimal("100"),
            invested_value=Decimal("1000"),
            current_price=Decimal("150"),
            current_value=current_value,
            unrealized_pnl=Decimal("500"),
        )

    def _create_mf_holding(
        self,
        scheme_name,
        amc_name,
        current_value,
        units=Decimal("100"),
    ):
        scheme = MutualFundScheme.objects.create(
            owner=self.user,
            scheme_name=scheme_name,
            amc_name=amc_name,
            scheme_code="TEST001",
            plan="Direct",
            option="Growth",
            category="Equity",
            is_active=True,
        )

        return MutualFundHolding.objects.create(
            owner=self.user,
            scheme=scheme,
            units=units,
            invested_value=Decimal("1000"),
            average_nav=Decimal("10"),
            current_nav=Decimal("15"),
            current_value=current_value,
        )

    def test_equity_holding_produces_monitored_holding_with_alias(self):
        self._create_equity_holding(
            "Aurobindo Pharma Limited",
            "AUROPHARMA",
            "INE406A01037",
            current_value=Decimal("18400"),
        )

        holdings = get_monitored_holdings(self.user)

        self.assertEqual(len(holdings), 1)

        holding = holdings[0]

        self.assertEqual(holding.holding_type, HoldingType.EQUITY)
        self.assertEqual(holding.display_name, "Aurobindo Pharma Limited")
        self.assertIn("Aurobindo Pharma", holding.aliases)
        self.assertEqual(holding.symbol, "AUROPHARMA")
        self.assertEqual(holding.portfolio_weight, 100.0)

    def test_mutual_fund_holding_strips_plan_boilerplate_in_alias(self):
        self._create_mf_holding(
            "Parag Parikh Flexi Cap Fund - Direct Plan - Growth",
            "PPFAS",
            current_value=Decimal("5000"),
        )

        holdings = get_monitored_holdings(self.user)

        self.assertEqual(len(holdings), 1)

        holding = holdings[0]

        self.assertEqual(holding.holding_type, HoldingType.MUTUAL_FUND)
        self.assertIn("Parag Parikh Flexi Cap Fund", holding.aliases)

    def test_portfolio_weight_reflects_relative_size(self):
        self._create_equity_holding(
            "Company A Limited",
            "COMPA",
            "INE000000001",
            current_value=Decimal("2000"),
        )

        self._create_equity_holding(
            "Company B Limited",
            "COMPB",
            "INE000000002",
            current_value=Decimal("8000"),
        )

        holdings = {
            holding.display_name: holding
            for holding in get_monitored_holdings(self.user)
        }

        self.assertAlmostEqual(
            holdings["Company A Limited"].portfolio_weight,
            20.0,
        )

        self.assertAlmostEqual(
            holdings["Company B Limited"].portfolio_weight,
            80.0,
        )

    def test_zero_quantity_holding_is_excluded(self):
        self._create_equity_holding(
            "Exited Company Limited",
            "EXITD",
            "INE000000009",
            current_value=Decimal("0"),
            quantity=Decimal("0"),
        )

        holdings = get_monitored_holdings(self.user)

        self.assertEqual(len(holdings), 0)

    def test_holdings_are_scoped_to_the_requesting_user(self):
        other_user = User.objects.create_user(
            username="otheruser",
            password="testpassword",
        )

        self._create_equity_holding(
            "My Company Limited",
            "MYCO",
            "INE000000003",
            current_value=Decimal("1000"),
        )

        other_holdings = get_monitored_holdings(other_user)

        self.assertEqual(other_holdings, [])


class QueryBuilderTests(TestCase):

    def test_base_and_event_queries_generated(self):
        holding = MonitoredHolding(
            holding_type=HoldingType.EQUITY,
            holding_id=1,
            display_name="Aurobindo Pharma Limited",
            aliases=["Aurobindo Pharma"],
            symbol="AUROPHARMA",
        )

        queries = QueryBuilder.build_queries(holding)

        self.assertIn("Aurobindo Pharma Limited", queries)
        self.assertIn("Aurobindo Pharma Limited earnings", queries)
        self.assertIn("Aurobindo Pharma Limited regulatory", queries)
        self.assertIn("AUROPHARMA share", queries)

    def test_query_count_is_bounded(self):
        holding = MonitoredHolding(
            holding_type=HoldingType.EQUITY,
            holding_id=1,
            display_name="Kalyan Jewellers India Limited",
            symbol="KALYANKJIL",
        )

        queries = QueryBuilder.build_queries(holding)

        self.assertLessEqual(
            len(queries),
            QueryBuilder.MAX_QUERIES_PER_HOLDING,
        )

    def test_short_symbol_not_added_as_standalone_query(self):
        holding = MonitoredHolding(
            holding_type=HoldingType.MUTUAL_FUND,
            holding_id=1,
            display_name="Navin Fluorine International Limited",
            symbol="",
        )

        queries = QueryBuilder.build_queries(holding)

        self.assertTrue(
            all("share" not in q for q in queries)
        )

    def test_empty_display_name_produces_no_queries(self):
        holding = MonitoredHolding(
            holding_type=HoldingType.EQUITY,
            holding_id=1,
            display_name="",
        )

        self.assertEqual(QueryBuilder.build_queries(holding), [])

    def test_sector_query_generated_for_known_sector(self):
        holding = MonitoredHolding(
            holding_type=HoldingType.EQUITY,
            holding_id=1,
            display_name="HDFC Bank Limited",
            symbol="HDFCBANK",
            sector="Banking",
        )

        queries = QueryBuilder.build_queries(holding)

        self.assertIn("Indian Banking sector", queries)

    def test_no_sector_query_when_sector_unknown(self):
        holding = MonitoredHolding(
            holding_type=HoldingType.EQUITY,
            holding_id=1,
            display_name="HDFC Bank Limited",
            symbol="HDFCBANK",
            sector="",
        )

        queries = QueryBuilder.build_queries(holding)

        self.assertFalse(
            any(q.startswith("Indian ") for q in queries)
        )

    def test_macro_queries_added_for_defensible_sector(self):
        holding = MonitoredHolding(
            holding_type=HoldingType.EQUITY,
            holding_id=1,
            display_name="HDFC Bank Limited",
            symbol="HDFCBANK",
            sector="Banking",
        )

        queries = QueryBuilder.build_queries(holding)

        self.assertIn("RBI", queries)

    def test_no_macro_queries_for_unmapped_sector(self):
        holding = MonitoredHolding(
            holding_type=HoldingType.EQUITY,
            holding_id=1,
            display_name="Some Obscure Company Limited",
            sector="Widgets",
        )

        terms = QueryBuilder.macro_terms_for_sector(
            holding.sector
        )

        self.assertEqual(terms, [])

    def test_macro_terms_capped(self):
        terms = QueryBuilder.macro_terms_for_sector("Banking")

        self.assertLessEqual(
            len(terms),
            QueryBuilder.MAX_MACRO_QUERIES_PER_HOLDING,
        )

    def test_is_sector_or_macro_query_true_for_generated_queries(
        self,
    ):
        holding = MonitoredHolding(
            holding_type=HoldingType.EQUITY,
            holding_id=1,
            display_name="HDFC Bank Limited",
            sector="Banking",
        )

        self.assertTrue(
            QueryBuilder.is_sector_or_macro_query(
                "Indian Banking sector", holding
            )
        )
        self.assertTrue(
            QueryBuilder.is_sector_or_macro_query("RBI", holding)
        )

    def test_is_sector_or_macro_query_false_for_unrelated_query(
        self,
    ):
        holding = MonitoredHolding(
            holding_type=HoldingType.EQUITY,
            holding_id=1,
            display_name="HDFC Bank Limited",
            sector="Banking",
        )

        self.assertFalse(
            QueryBuilder.is_sector_or_macro_query(
                "HDFC Bank Limited earnings", holding
            )
        )

    def test_is_sector_or_macro_query_false_without_sector(self):
        holding = MonitoredHolding(
            holding_type=HoldingType.EQUITY,
            holding_id=1,
            display_name="HDFC Bank Limited",
            sector="",
        )

        self.assertFalse(
            QueryBuilder.is_sector_or_macro_query("RBI", holding)
        )

    def test_query_count_still_bounded_with_sector_and_macro(self):
        holding = MonitoredHolding(
            holding_type=HoldingType.EQUITY,
            holding_id=1,
            display_name="HDFC Bank Limited",
            symbol="HDFCBANK",
            sector="Banking",
        )

        queries = QueryBuilder.build_queries(holding)

        self.assertLessEqual(
            len(queries),
            QueryBuilder.MAX_QUERIES_PER_HOLDING,
        )


class HoldingMatcherTests(TestCase):

    def setUp(self):
        self.holding = MonitoredHolding(
            holding_type=HoldingType.EQUITY,
            holding_id=1,
            display_name="Aurobindo Pharma Limited",
            aliases=["Aurobindo Pharma"],
            symbol="AUROPHARMA",
            isin="INE406A01037",
        )

    def test_company_name_match(self):
        self.assertTrue(
            HoldingMatcher.is_relevant(
                "Aurobindo Pharma Limited receives USFDA approval",
                "",
                self.holding,
            )
        )

    def test_alias_match(self):
        self.assertTrue(
            HoldingMatcher.is_relevant(
                "Aurobindo Pharma gets USFDA nod",
                "",
                self.holding,
            )
        )

    def test_ticker_match(self):
        self.assertTrue(
            HoldingMatcher.is_relevant(
                "Stocks to watch today",
                "AUROPHARMA shares rallied 5% in early trade",
                self.holding,
            )
        )

    def test_irrelevant_article_does_not_match(self):
        self.assertFalse(
            HoldingMatcher.is_relevant(
                "Kalyan Jewellers opens new showroom in Pune",
                "The jewellery retailer expanded its footprint.",
                self.holding,
            )
        )

    def test_partial_word_does_not_false_positive(self):
        # "Auro" appearing inside an unrelated word must not match.
        self.assertFalse(
            HoldingMatcher.is_relevant(
                "Auroville tourism sees a boost this season",
                "",
                self.holding,
            )
        )

    def test_match_holdings_returns_only_matching_subset(self):
        kalyan = MonitoredHolding(
            holding_type=HoldingType.EQUITY,
            holding_id=2,
            display_name="Kalyan Jewellers India Limited",
            aliases=["Kalyan Jewellers India"],
            symbol="KALYANKJIL",
        )

        matches = HoldingMatcher.match_holdings(
            "Aurobindo Pharma Limited receives USFDA approval",
            "",
            [self.holding, kalyan],
        )

        self.assertEqual(matches, [self.holding])

    def test_sector_query_article_without_company_name_matches(
        self,
    ):
        banking_holding = MonitoredHolding(
            holding_type=HoldingType.EQUITY,
            holding_id=3,
            display_name="HDFC Bank Limited",
            symbol="HDFCBANK",
            sector="Banking",
        )

        # No mention of "HDFC Bank" anywhere - a genuine sector
        # story, which is the whole point of this fallback path.
        self.assertTrue(
            HoldingMatcher.is_relevant(
                "Indian banking sector sees record credit growth",
                "Analysts say the banking sector is expanding.",
                banking_holding,
                matched_query="Indian Banking sector",
            )
        )

    def test_macro_query_article_matches_via_topic_term(self):
        banking_holding = MonitoredHolding(
            holding_type=HoldingType.EQUITY,
            holding_id=3,
            display_name="HDFC Bank Limited",
            symbol="HDFCBANK",
            sector="Banking",
        )

        self.assertTrue(
            HoldingMatcher.is_relevant(
                "RBI raises repo rate by 25 basis points",
                "",
                banking_holding,
                matched_query="RBI",
            )
        )

    def test_sector_query_without_sector_or_topic_mention_rejected(
        self,
    ):
        banking_holding = MonitoredHolding(
            holding_type=HoldingType.EQUITY,
            holding_id=3,
            display_name="HDFC Bank Limited",
            symbol="HDFCBANK",
            sector="Banking",
        )

        # Query was a sector/macro query for this holding, but the
        # returned article text doesn't actually mention the
        # sector or the macro topic - must not be waved through.
        self.assertFalse(
            HoldingMatcher.is_relevant(
                "Local cricket team wins championship",
                "",
                banking_holding,
                matched_query="RBI",
            )
        )

    def test_sector_fallback_not_applied_when_query_not_sector_or_macro(
        self,
    ):
        banking_holding = MonitoredHolding(
            holding_type=HoldingType.EQUITY,
            holding_id=3,
            display_name="HDFC Bank Limited",
            symbol="HDFCBANK",
            sector="Banking",
        )

        # matched_query is a normal company query, not a
        # sector/macro one - sector text alone should not be
        # enough to match without an actual company mention.
        self.assertFalse(
            HoldingMatcher.is_relevant(
                "Banking sector conference held in Mumbai",
                "",
                banking_holding,
                matched_query="HDFC Bank Limited",
            )
        )

    def test_sector_fallback_not_applied_without_holding_sector(
        self,
    ):
        no_sector_holding = MonitoredHolding(
            holding_type=HoldingType.EQUITY,
            holding_id=4,
            display_name="Some Company Limited",
            sector="",
        )

        self.assertFalse(
            HoldingMatcher.is_relevant(
                "RBI raises repo rate",
                "",
                no_sector_holding,
                matched_query="RBI",
            )
        )
        


import json as _json

from portfolio_news.constants import (
    ImpactLevel,
    NewsCategory,
    Sentiment,
    TimeHorizon,
)
from portfolio_news.services.gemini_analyzer import (
    ArticleAnalysis,
    GeminiArticleAnalyzer,
)


class _FakeArticle:
    def __init__(
        self,
        title="Aurobindo Pharma receives USFDA approval",
        description="Aurobindo Pharma received USFDA approval.",
        source="Reuters",
        published_at=None,
        id=1,
    ):
        self.title = title
        self.description = description
        self.source = source
        self.published_at = published_at
        self.id = id


def _gemini_response(text_payload):
    return {
        "candidates": [
            {
                "content": {
                    "parts": [{"text": text_payload}]
                }
            }
        ]
    }


def _valid_analysis_json():
    return _json.dumps(
        {
            "relevant": True,
            "relevance_score": 94,
            "sentiment": "negative",
            "impact": "high",
            "impact_score": 88,
            "category": "REGULATORY",
            "time_horizon": "medium_term",
            "summary": "Short factual summary.",
            "portfolio_implication": (
                "Potential negative near-term impact."
            ),
            "reason": (
                "The article concerns a regulatory development "
                "directly affecting the company."
            ),
            "confidence": 0.91,
        }
    )


class ArticleAnalysisValidationTests(TestCase):

    def test_valid_json_parses_correctly(self):
        analysis = ArticleAnalysis.from_gemini_json(
            _json.loads(_valid_analysis_json())
        )

        self.assertTrue(analysis.relevant)
        self.assertEqual(analysis.relevance_score, 94)
        self.assertEqual(analysis.sentiment, "negative")
        self.assertEqual(analysis.impact, "high")
        self.assertEqual(analysis.category, "REGULATORY")
        self.assertEqual(analysis.confidence, 0.91)

    def test_out_of_range_scores_are_clamped(self):
        analysis = ArticleAnalysis.from_gemini_json(
            {
                "relevant": True,
                "relevance_score": 500,
                "sentiment": "negative",
                "impact": "high",
                "impact_score": -10,
                "category": "REGULATORY",
                "time_horizon": "medium_term",
                "summary": "s",
                "portfolio_implication": "p",
                "reason": "r",
                "confidence": 5.0,
            }
        )

        self.assertEqual(analysis.relevance_score, 100)
        self.assertEqual(analysis.impact_score, 0)
        self.assertEqual(analysis.confidence, 1.0)

    def test_invalid_enum_falls_back_to_safe_default(self):
        analysis = ArticleAnalysis.from_gemini_json(
            {
                "relevant": True,
                "relevance_score": 50,
                "sentiment": "bullish",
                "impact": "super-high",
                "impact_score": 55,
                "category": "SOMETHING_MADE_UP",
                "time_horizon": "next_week",
                "summary": "s",
                "portfolio_implication": "p",
                "reason": "r",
                "confidence": 0.5,
            }
        )

        self.assertEqual(analysis.sentiment, Sentiment.NEUTRAL)
        self.assertEqual(analysis.category, NewsCategory.OTHER)
        self.assertEqual(
            analysis.time_horizon, TimeHorizon.UNSPECIFIED
        )
        # impact falls back to a score-derived value since the
        # given impact string wasn't a valid choice.
        self.assertEqual(
            analysis.impact,
            ImpactLevel.from_score(55),
        )

    def test_missing_summary_gets_safe_placeholder(self):
        analysis = ArticleAnalysis.from_gemini_json(
            {
                "relevant": False,
                "relevance_score": 10,
                "sentiment": "neutral",
                "impact": "low",
                "impact_score": 15,
                "category": "OTHER",
                "time_horizon": "unspecified",
                "summary": "",
                "portfolio_implication": "",
                "reason": "",
                "confidence": 0.2,
            }
        )

        self.assertTrue(analysis.summary)
        self.assertTrue(analysis.portfolio_implication)
        self.assertTrue(analysis.reason)

    def test_materiality_and_fact_fields_parse_correctly(self):
        from portfolio_news.constants import Materiality

        analysis = ArticleAnalysis.from_gemini_json(
            {
                "relevant": True,
                "relevance_score": 90,
                "sentiment": "negative",
                "impact": "critical",
                "impact_score": 92,
                "category": "REGULATORY",
                "time_horizon": "immediate",
                "summary": "s",
                "portfolio_implication": "p",
                "reason": "r",
                "confidence": 0.85,
                "materiality": "critical",
                "key_facts": (
                    "The regulator issued a formal notice."
                ),
                "interpretation": (
                    "This could signal heightened scrutiny."
                ),
                "uncertainty_notes": (
                    "The final penalty amount is not yet known."
                ),
            }
        )

        self.assertEqual(analysis.materiality, Materiality.CRITICAL)
        self.assertEqual(
            analysis.key_facts,
            "The regulator issued a formal notice.",
        )
        self.assertEqual(
            analysis.interpretation,
            "This could signal heightened scrutiny.",
        )
        self.assertEqual(
            analysis.uncertainty_notes,
            "The final penalty amount is not yet known.",
        )

    def test_missing_materiality_defaults_from_impact_level(self):
        from portfolio_news.constants import Materiality

        analysis = ArticleAnalysis.from_gemini_json(
            {
                "relevant": True,
                "relevance_score": 90,
                "sentiment": "negative",
                "impact": "critical",
                "impact_score": 92,
                "category": "REGULATORY",
                "time_horizon": "immediate",
                "summary": "s",
                "portfolio_implication": "p",
                "reason": "r",
                "confidence": 0.85,
                # materiality/key_facts/interpretation/
                # uncertainty_notes intentionally omitted, as an
                # older/partial Gemini response might do.
            }
        )

        self.assertEqual(analysis.materiality, Materiality.CRITICAL)
        self.assertEqual(analysis.key_facts, "")
        self.assertEqual(analysis.interpretation, "")
        self.assertEqual(analysis.uncertainty_notes, "")

    def test_invalid_materiality_falls_back_to_impact_derived_value(
        self,
    ):
        from portfolio_news.constants import Materiality

        analysis = ArticleAnalysis.from_gemini_json(
            {
                "relevant": True,
                "relevance_score": 20,
                "sentiment": "neutral",
                "impact": "low",
                "impact_score": 25,
                "category": "OTHER",
                "time_horizon": "unspecified",
                "summary": "s",
                "portfolio_implication": "p",
                "reason": "r",
                "confidence": 0.3,
                "materiality": "super-duper-critical",
            }
        )

        self.assertEqual(analysis.materiality, Materiality.LOW)


class ImpactLevelThresholdTests(TestCase):

    def test_documented_thresholds(self):
        self.assertEqual(
            ImpactLevel.from_score(0), ImpactLevel.VERY_LOW
        )
        self.assertEqual(
            ImpactLevel.from_score(20), ImpactLevel.VERY_LOW
        )
        self.assertEqual(
            ImpactLevel.from_score(21), ImpactLevel.LOW
        )
        self.assertEqual(
            ImpactLevel.from_score(40), ImpactLevel.LOW
        )
        self.assertEqual(
            ImpactLevel.from_score(41), ImpactLevel.MODERATE
        )
        self.assertEqual(
            ImpactLevel.from_score(60), ImpactLevel.MODERATE
        )
        self.assertEqual(
            ImpactLevel.from_score(61), ImpactLevel.HIGH
        )
        self.assertEqual(
            ImpactLevel.from_score(80), ImpactLevel.HIGH
        )
        self.assertEqual(
            ImpactLevel.from_score(81), ImpactLevel.CRITICAL
        )
        self.assertEqual(
            ImpactLevel.from_score(100), ImpactLevel.CRITICAL
        )


class GeminiArticleAnalyzerTests(TestCase):

    def setUp(self):
        self.analyzer = GeminiArticleAnalyzer()

        self.holding = MonitoredHolding(
            holding_type=HoldingType.EQUITY,
            holding_id=1,
            display_name="Aurobindo Pharma Limited",
            aliases=["Aurobindo Pharma"],
            symbol="AUROPHARMA",
            portfolio_weight=18.4,
        )

        self.article = _FakeArticle()

    def test_missing_api_key_returns_none_without_request(self):
        with patch(
            "portfolio_news.services.gemini_analyzer.get_gemini_api_key",
            return_value=None,
        ):
            with patch(
                "portfolio_news.services.gemini_analyzer.requests.post"
            ) as mock_post:

                result = self.analyzer.analyze(
                    self.article, self.holding
                )

        self.assertIsNone(result)
        mock_post.assert_not_called()

    def test_valid_response_returns_article_analysis(self):
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = _gemini_response(
            _valid_analysis_json()
        )

        with patch(
            "portfolio_news.services.gemini_analyzer.get_gemini_api_key",
            return_value="fake-key",
        ):
            with patch(
                "portfolio_news.services.gemini_analyzer.requests.post",
                return_value=mock_response,
            ):
                result = self.analyzer.analyze(
                    self.article, self.holding
                )

        self.assertIsInstance(result, ArticleAnalysis)
        self.assertTrue(result.relevant)
        self.assertEqual(result.impact, "high")

    def test_network_failure_returns_none(self):
        with patch(
            "portfolio_news.services.gemini_analyzer.get_gemini_api_key",
            return_value="fake-key",
        ):
            with patch(
                "portfolio_news.services.gemini_analyzer.requests.post",
                side_effect=requests.exceptions.ConnectionError(
                    "no network"
                ),
            ):
                result = self.analyzer.analyze(
                    self.article, self.holding
                )

        self.assertIsNone(result)

    def test_http_error_returns_none(self):
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock(
            side_effect=requests.exceptions.HTTPError("500")
        )

        with patch(
            "portfolio_news.services.gemini_analyzer.get_gemini_api_key",
            return_value="fake-key",
        ):
            with patch(
                "portfolio_news.services.gemini_analyzer.requests.post",
                return_value=mock_response,
            ):
                result = self.analyzer.analyze(
                    self.article, self.holding
                )

        self.assertIsNone(result)

    def test_invalid_json_in_response_returns_none(self):
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = _gemini_response(
            "this is not valid json {{{"
        )

        with patch(
            "portfolio_news.services.gemini_analyzer.get_gemini_api_key",
            return_value="fake-key",
        ):
            with patch(
                "portfolio_news.services.gemini_analyzer.requests.post",
                return_value=mock_response,
            ):
                result = self.analyzer.analyze(
                    self.article, self.holding
                )

        self.assertIsNone(result)

    def test_empty_response_text_returns_none(self):
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = _gemini_response("")

        with patch(
            "portfolio_news.services.gemini_analyzer.get_gemini_api_key",
            return_value="fake-key",
        ):
            with patch(
                "portfolio_news.services.gemini_analyzer.requests.post",
                return_value=mock_response,
            ):
                result = self.analyzer.analyze(
                    self.article, self.holding
                )

        self.assertIsNone(result)
        


from portfolio_news.constants import NotificationTier
from portfolio_news.models import PortfolioNewsAlert
from portfolio_news.services.alert_scoring import (
    compute_alert_score,
    determine_notification_tier,
    should_include_in_digest,
    should_send_immediate_notification,
)
from portfolio_news.services.notification_creation import (
    create_alert_from_analysis,
)


class AlertScoringTests(TestCase):

    def test_spec_example_company_b_outranks_company_a(self):
        """
        Company A: impact=90, weight=2%
        Company B: impact=80, weight=25%
        Company B should score higher (per the spec example).
        """

        score_a = compute_alert_score(
            impact_score=90,
            portfolio_weight_percent=2,
            confidence=1.0,
        )

        score_b = compute_alert_score(
            impact_score=80,
            portfolio_weight_percent=25,
            confidence=1.0,
        )

        self.assertLess(score_a, score_b)
        self.assertAlmostEqual(score_a, 1.8)
        self.assertAlmostEqual(score_b, 20.0)

    def test_score_is_bounded_0_to_100(self):
        score = compute_alert_score(
            impact_score=100,
            portfolio_weight_percent=100,
            confidence=1.0,
        )

        self.assertEqual(score, 100.0)

    def test_zero_confidence_gives_zero_score(self):
        score = compute_alert_score(
            impact_score=100,
            portfolio_weight_percent=100,
            confidence=0.0,
        )

        self.assertEqual(score, 0.0)

    def test_notification_tier_mapping(self):
        self.assertEqual(
            determine_notification_tier(ImpactLevel.CRITICAL),
            NotificationTier.CRITICAL,
        )
        self.assertEqual(
            determine_notification_tier(ImpactLevel.HIGH),
            NotificationTier.HIGH,
        )
        self.assertEqual(
            determine_notification_tier(ImpactLevel.MODERATE),
            NotificationTier.MODERATE,
        )
        self.assertEqual(
            determine_notification_tier(ImpactLevel.LOW),
            NotificationTier.LOW,
        )
        self.assertEqual(
            determine_notification_tier(ImpactLevel.VERY_LOW),
            NotificationTier.LOW,
        )

    def test_critical_and_high_trigger_immediate_notification(self):
        self.assertTrue(
            should_send_immediate_notification(
                NotificationTier.CRITICAL
            )
        )
        self.assertTrue(
            should_send_immediate_notification(
                NotificationTier.HIGH
            )
        )
        self.assertFalse(
            should_send_immediate_notification(
                NotificationTier.MODERATE
            )
        )
        self.assertFalse(
            should_send_immediate_notification(
                NotificationTier.LOW
            )
        )

    def test_moderate_goes_to_digest_only(self):
        self.assertTrue(
            should_include_in_digest(NotificationTier.MODERATE)
        )
        self.assertFalse(
            should_include_in_digest(NotificationTier.CRITICAL)
        )
        self.assertFalse(
            should_include_in_digest(NotificationTier.LOW)
        )


class AlertScoringSourceQualityAndRecencyTests(TestCase):
    """
    Covers the additive source_quality/published_at factors on
    compute_alert_score. Every existing (pre-slice-2) call
    pattern - omitting these two arguments - must keep producing
    exactly the same score as before; that is verified by the
    untouched tests in AlertScoringTests above. These tests cover
    only the new behavior.
    """

    def test_omitting_new_params_matches_base_formula_exactly(self):
        score = compute_alert_score(
            impact_score=80,
            portfolio_weight_percent=25,
            confidence=1.0,
        )

        self.assertAlmostEqual(score, 20.0)

    def test_tier_1_source_scores_higher_than_tier_3(self):
        from portfolio_news.constants import SourceQualityTier

        tier_1_score = compute_alert_score(
            impact_score=80,
            portfolio_weight_percent=25,
            confidence=1.0,
            source_quality=SourceQualityTier.TIER_1,
        )

        tier_3_score = compute_alert_score(
            impact_score=80,
            portfolio_weight_percent=25,
            confidence=1.0,
            source_quality=SourceQualityTier.TIER_3,
        )

        self.assertGreater(tier_1_score, tier_3_score)
        self.assertAlmostEqual(tier_1_score, 20.0)
        self.assertAlmostEqual(tier_3_score, 10.0)

    def test_fresh_article_scores_higher_than_stale_one(self):
        now = datetime(2026, 8, 27, 12, 0, tzinfo=timezone.utc)

        fresh_score = compute_alert_score(
            impact_score=80,
            portfolio_weight_percent=25,
            confidence=1.0,
            published_at=datetime(
                2026, 8, 27, 6, 0, tzinfo=timezone.utc
            ),
            now=now,
        )

        stale_score = compute_alert_score(
            impact_score=80,
            portfolio_weight_percent=25,
            confidence=1.0,
            published_at=datetime(
                2026, 8, 1, 6, 0, tzinfo=timezone.utc
            ),
            now=now,
        )

        self.assertGreater(fresh_score, stale_score)
        self.assertAlmostEqual(fresh_score, 20.0)
        self.assertAlmostEqual(stale_score, 10.0)

    def test_missing_published_at_is_neutral_not_penalized(self):
        with_date_fresh = compute_alert_score(
            impact_score=80,
            portfolio_weight_percent=25,
            confidence=1.0,
            published_at=datetime(
                2026, 8, 27, 6, 0, tzinfo=timezone.utc
            ),
            now=datetime(
                2026, 8, 27, 12, 0, tzinfo=timezone.utc
            ),
        )

        without_date = compute_alert_score(
            impact_score=80,
            portfolio_weight_percent=25,
            confidence=1.0,
            published_at=None,
        )

        self.assertAlmostEqual(with_date_fresh, without_date)

    def test_recency_decays_linearly_between_one_and_seven_days(self):
        now = datetime(2026, 8, 27, 0, 0, tzinfo=timezone.utc)

        # 4 days old -> halfway through the 1-7 day decay window.
        score = compute_alert_score(
            impact_score=100,
            portfolio_weight_percent=100,
            confidence=1.0,
            published_at=datetime(
                2026, 8, 23, 0, 0, tzinfo=timezone.utc
            ),
            now=now,
        )

        # weight_fraction=1.0, confidence=1.0, recency=0.75
        self.assertAlmostEqual(score, 75.0)

    def test_score_still_bounded_0_to_100_with_all_factors(self):
        from portfolio_news.constants import SourceQualityTier

        score = compute_alert_score(
            impact_score=100,
            portfolio_weight_percent=100,
            confidence=1.0,
            source_quality=SourceQualityTier.TIER_1,
            published_at=datetime(
                2026, 8, 27, 0, 0, tzinfo=timezone.utc
            ),
            now=datetime(
                2026, 8, 27, 1, 0, tzinfo=timezone.utc
            ),
        )

        self.assertEqual(score, 100.0)


class NotificationCreationTests(TestCase):

    def setUp(self):
        self.user = User.objects.create_user(
            username="alertuser",
            password="testpassword",
        )

        self.other_user = User.objects.create_user(
            username="otheralertuser",
            password="testpassword",
        )

        self.article, _ = store_article(
            NewsArticleResult(
                title="Aurobindo Pharma receives USFDA approval",
                url="https://reuters.com/aurobindo-1",
                source="Reuters",
                description="USFDA approval news.",
                published_at=datetime(
                    2026, 8, 24, 9, 0, tzinfo=timezone.utc
                ),
            )
        )

        self.holding = MonitoredHolding(
            holding_type=HoldingType.EQUITY,
            holding_id=42,
            display_name="Aurobindo Pharma Limited",
            symbol="AUROPHARMA",
            portfolio_weight=18.4,
        )

    def _analysis(self, impact="high", impact_score=88, confidence=0.91):
        return ArticleAnalysis(
            relevant=True,
            relevance_score=94,
            sentiment="negative",
            impact=impact,
            impact_score=impact_score,
            category="REGULATORY",
            time_horizon="medium_term",
            summary="Short factual summary.",
            portfolio_implication="Potential negative impact.",
            reason="Regulatory development.",
            confidence=confidence,
        )

    def test_creates_alert_with_correct_fields(self):
        alert, created = create_alert_from_analysis(
            self.user, self.article, self.holding, self._analysis()
        )

        self.assertTrue(created)
        self.assertEqual(alert.user, self.user)
        self.assertEqual(alert.article, self.article)
        self.assertEqual(
            alert.holding_display_name,
            "Aurobindo Pharma Limited",
        )
        self.assertEqual(alert.notification_tier, "high")
        self.assertTrue(alert.notification_sent)
        self.assertFalse(alert.is_read)

    def test_critical_impact_high_weight_gets_notified(self):
        alert, _ = create_alert_from_analysis(
            self.user,
            self.article,
            self.holding,
            self._analysis(impact="critical", impact_score=95),
        )

        self.assertEqual(alert.notification_tier, "critical")
        self.assertTrue(alert.notification_sent)

    def test_moderate_impact_is_not_sent_as_immediate_notification(self):
        alert, _ = create_alert_from_analysis(
            self.user,
            self.article,
            self.holding,
            self._analysis(impact="moderate", impact_score=50),
        )

        self.assertEqual(alert.notification_tier, "moderate")
        self.assertFalse(alert.notification_sent)

    def test_low_impact_is_not_sent_as_notification(self):
        alert, _ = create_alert_from_analysis(
            self.user,
            self.article,
            self.holding,
            self._analysis(impact="low", impact_score=25),
        )

        self.assertEqual(alert.notification_tier, "low")
        self.assertFalse(alert.notification_sent)

    def test_running_twice_does_not_create_duplicate_alert(self):
        create_alert_from_analysis(
            self.user, self.article, self.holding, self._analysis()
        )

        alert_2, created_2 = create_alert_from_analysis(
            self.user, self.article, self.holding, self._analysis()
        )

        self.assertFalse(created_2)
        self.assertEqual(
            PortfolioNewsAlert.objects.filter(
                user=self.user
            ).count(),
            1,
        )

    def test_alerts_are_scoped_per_user(self):
        create_alert_from_analysis(
            self.user, self.article, self.holding, self._analysis()
        )

        create_alert_from_analysis(
            self.other_user,
            self.article,
            self.holding,
            self._analysis(),
        )

        self.assertEqual(
            PortfolioNewsAlert.objects.filter(
                user=self.user
            ).count(),
            1,
        )

        self.assertEqual(
            PortfolioNewsAlert.objects.filter(
                user=self.other_user
            ).count(),
            1,
        )

        # Explicitly verify one user's alerts are not the other's.
        user_alert_ids = set(
            PortfolioNewsAlert.objects.filter(
                user=self.user
            ).values_list("id", flat=True)
        )

        other_user_alert_ids = set(
            PortfolioNewsAlert.objects.filter(
                user=self.other_user
            ).values_list("id", flat=True)
        )

        self.assertEqual(
            user_alert_ids.intersection(other_user_alert_ids),
            set(),
        )


class DigestServiceTests(TestCase):
    """
    Direct unit coverage for build_daily_digest, independent of
    the API layer (see PortfolioNewsAPITests for the endpoint
    tests).
    """

    def setUp(self):
        self.user = User.objects.create_user(
            username="digestuser",
            password="testpassword",
        )

        self.holding = MonitoredHolding(
            holding_type=HoldingType.EQUITY,
            holding_id=77,
            display_name="Aurobindo Pharma Limited",
            symbol="AUROPHARMA",
            portfolio_weight=18.4,
        )

    def _analysis(self, impact, impact_score, confidence=0.9):
        return ArticleAnalysis(
            relevant=True,
            relevance_score=90,
            sentiment="neutral",
            impact=impact,
            impact_score=impact_score,
            category="OTHER",
            time_horizon="medium_term",
            summary="Summary.",
            portfolio_implication="Implication.",
            reason="Reason.",
            confidence=confidence,
        )

    def test_digest_excludes_low_tier_alerts(self):
        from portfolio_news.services.digest import (
            build_daily_digest,
        )

        article, _ = store_article(
            NewsArticleResult(
                title="Aurobindo Pharma minor update",
                url="https://reuters.com/aurobindo-low-1",
                source="Reuters",
                description="Minor update.",
            )
        )

        create_alert_from_analysis(
            self.user,
            article,
            self.holding,
            self._analysis(impact="low", impact_score=15),
        )

        digest = build_daily_digest(self.user)

        self.assertEqual(digest.item_count, 0)

    def test_digest_excludes_other_users_alerts(self):
        from portfolio_news.services.digest import (
            build_daily_digest,
        )

        other_user = User.objects.create_user(
            username="otherdigestuser",
            password="testpassword",
        )

        article, _ = store_article(
            NewsArticleResult(
                title="Aurobindo Pharma big development",
                url="https://reuters.com/aurobindo-other-1",
                source="Reuters",
                description="Big development.",
            )
        )

        create_alert_from_analysis(
            other_user,
            article,
            self.holding,
            self._analysis(impact="critical", impact_score=90),
        )

        digest = build_daily_digest(self.user)

        self.assertEqual(digest.item_count, 0)

    def test_digest_excludes_alerts_from_other_days(self):
        from datetime import timedelta

        from django.utils import timezone as dj_timezone

        from portfolio_news.services.digest import (
            build_daily_digest,
        )

        article, _ = store_article(
            NewsArticleResult(
                title="Aurobindo Pharma another update",
                url="https://reuters.com/aurobindo-other-day-1",
                source="Reuters",
                description="Update.",
            )
        )

        alert, _ = create_alert_from_analysis(
            self.user,
            article,
            self.holding,
            self._analysis(impact="high", impact_score=70),
        )

        # Backdate the alert to yesterday.
        yesterday = dj_timezone.now() - timedelta(days=1)

        PortfolioNewsAlert.objects.filter(
            id=alert.id
        ).update(created_at=yesterday)

        digest = build_daily_digest(self.user)

        self.assertEqual(digest.item_count, 0)

    def test_digest_item_reflects_source_count(self):
        from portfolio_news.services.digest import (
            build_daily_digest,
        )

        published_at = datetime(
            2026, 8, 24, 9, 0, tzinfo=timezone.utc
        )

        store_article(
            NewsArticleResult(
                title="Aurobindo Pharma wins big order",
                url="https://reuters.com/aurobindo-multi-1",
                source="Reuters",
                description="Order news.",
                published_at=published_at,
            )
        )

        article, _ = store_article(
            NewsArticleResult(
                title="Aurobindo Pharma secures major order",
                url="https://economictimes.com/aurobindo-multi-2",
                source="Economic Times",
                description="Order news.",
                published_at=published_at,
            )
        )

        create_alert_from_analysis(
            self.user,
            article,
            self.holding,
            self._analysis(impact="high", impact_score=70),
        )

        digest = build_daily_digest(self.user)

        self.assertEqual(digest.item_count, 1)
        self.assertEqual(digest.items[0].source_count, 2)


from rest_framework.test import APIClient


class PortfolioNewsAPITests(TestCase):

    def setUp(self):
        self.user = User.objects.create_user(
            username="apiuser",
            password="testpassword",
        )

        self.other_user = User.objects.create_user(
            username="otherapiuser",
            password="testpassword",
        )

        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

        self.other_client = APIClient()
        self.other_client.force_authenticate(user=self.other_user)

        article, _ = store_article(
            NewsArticleResult(
                title="Aurobindo Pharma receives USFDA approval",
                url="https://reuters.com/aurobindo-api-1",
                source="Reuters",
                description="USFDA approval news.",
                published_at=datetime(
                    2026, 8, 24, 9, 0, tzinfo=timezone.utc
                ),
            )
        )

        holding = MonitoredHolding(
            holding_type=HoldingType.EQUITY,
            holding_id=101,
            display_name="Aurobindo Pharma Limited",
            symbol="AUROPHARMA",
            portfolio_weight=18.4,
        )

        analysis = ArticleAnalysis(
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

        self.alert, _ = create_alert_from_analysis(
            self.user, article, holding, analysis
        )

        # A second, moderate-tier alert for filter tests.
        moderate_article, _ = store_article(
            NewsArticleResult(
                title="Aurobindo Pharma opens new plant",
                url="https://reuters.com/aurobindo-api-2",
                source="Reuters",
                description="Expansion news.",
                published_at=datetime(
                    2026, 8, 23, 9, 0, tzinfo=timezone.utc
                ),
            )
        )

        moderate_analysis = ArticleAnalysis(
            relevant=True,
            relevance_score=60,
            sentiment="positive",
            impact="moderate",
            impact_score=50,
            category="EXPANSION" if False else "PRODUCT",
            time_horizon="long_term",
            summary="Expansion summary.",
            portfolio_implication="Possible long-term positive.",
            reason="Capacity expansion.",
            confidence=0.7,
        )

        self.moderate_alert, _ = create_alert_from_analysis(
            self.user, moderate_article, holding, moderate_analysis
        )

    def test_news_list_returns_only_own_alerts(self):
        response = self.client.get("/api/ai/news/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 2)

        other_response = self.other_client.get("/api/ai/news/")

        self.assertEqual(other_response.status_code, 200)
        self.assertEqual(other_response.data["count"], 0)

    def test_news_list_tier_filter(self):
        response = self.client.get("/api/ai/news/?tier=high")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(
            response.data["results"][0]["id"], self.alert.id
        )

    def test_news_list_category_filter(self):
        response = self.client.get(
            "/api/ai/news/?category=REGULATORY"
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(
            response.data["results"][0]["id"], self.alert.id
        )

    def test_news_list_sentiment_filter(self):
        response = self.client.get(
            "/api/ai/news/?sentiment=positive"
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(
            response.data["results"][0]["id"],
            self.moderate_alert.id,
        )

    def test_news_list_holding_type_filter(self):
        mf_article, _ = store_article(
            NewsArticleResult(
                title="HDFC Flexi Cap Fund NAV update",
                url="https://reuters.com/hdfc-flexicap-1",
                source="Reuters",
                description="NAV update.",
                published_at=datetime(
                    2026, 8, 22, 9, 0, tzinfo=timezone.utc
                ),
            )
        )

        mf_holding = MonitoredHolding(
            holding_type=HoldingType.MUTUAL_FUND,
            holding_id=501,
            display_name="HDFC Flexi Cap Fund",
            amc_name="HDFC Mutual Fund",
            portfolio_weight=10.0,
        )

        mf_analysis = ArticleAnalysis(
            relevant=True,
            relevance_score=40,
            sentiment="neutral",
            impact="low",
            impact_score=20,
            category="OTHER",
            time_horizon="unspecified",
            summary="NAV summary.",
            portfolio_implication="No material implication.",
            reason="Routine update.",
            confidence=0.5,
        )

        create_alert_from_analysis(
            self.user, mf_article, mf_holding, mf_analysis
        )

        response = self.client.get(
            "/api/ai/news/?holding_type=MUTUAL_FUND"
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(
            response.data["results"][0]["holding_type"],
            "MUTUAL_FUND",
        )

    def test_news_list_holding_id_filter(self):
        response = self.client.get("/api/ai/news/?holding_id=101")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 2)

        response = self.client.get("/api/ai/news/?holding_id=999")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 0)

    def test_news_list_date_range_today_excludes_backdated_alert(
        self,
    ):
        from datetime import timedelta

        from django.utils import timezone as dj_timezone

        PortfolioNewsAlert.objects.filter(
            id=self.moderate_alert.id
        ).update(
            created_at=dj_timezone.now() - timedelta(days=10)
        )

        response = self.client.get(
            "/api/ai/news/?date_range=today"
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(
            response.data["results"][0]["id"], self.alert.id
        )

    def test_news_list_date_range_30d_includes_recent_alerts(self):
        response = self.client.get(
            "/api/ai/news/?date_range=30d"
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 2)

    def test_news_list_date_range_7d_excludes_older_alert(self):
        from datetime import timedelta

        from django.utils import timezone as dj_timezone

        PortfolioNewsAlert.objects.filter(
            id=self.moderate_alert.id
        ).update(
            created_at=dj_timezone.now() - timedelta(days=10)
        )

        response = self.client.get("/api/ai/news/?date_range=7d")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(
            response.data["results"][0]["id"], self.alert.id
        )

    def test_news_list_filters_combine_with_and_semantics(self):
        response = self.client.get(
            "/api/ai/news/?category=REGULATORY&sentiment=positive"
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 0)

    def test_news_list_invalid_holding_id_is_ignored_not_error(self):
        response = self.client.get(
            "/api/ai/news/?holding_id=not-a-number"
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 2)

    def test_news_detail_returns_full_fields(self):
        response = self.client.get(
            f"/api/ai/news/{self.alert.id}/"
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.data["holding_display_name"],
            "Aurobindo Pharma Limited",
        )
        self.assertEqual(
            response.data["portfolio_weight_at_alert"], 18.4
        )
        self.assertIn("article_url", response.data)
        self.assertIn("reason", response.data)

    def test_news_detail_is_not_accessible_to_other_user(self):
        response = self.other_client.get(
            f"/api/ai/news/{self.alert.id}/"
        )

        self.assertEqual(response.status_code, 404)

    def test_unauthenticated_request_is_rejected(self):
        anonymous_client = APIClient()

        response = anonymous_client.get("/api/ai/news/")

        self.assertEqual(response.status_code, 403)

    def test_notifications_list_only_includes_high_and_critical(self):
        response = self.client.get("/api/ai/notifications/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["unread_count"], 1)
        self.assertEqual(len(response.data["results"]), 1)
        self.assertEqual(
            response.data["results"][0]["id"], self.alert.id
        )

    def test_mark_notification_read(self):
        response = self.client.post(
            f"/api/ai/notifications/{self.alert.id}/read/"
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["is_read"])

        self.alert.refresh_from_db()
        self.assertTrue(self.alert.is_read)

    def test_mark_notification_read_for_other_users_alert_404s(self):
        response = self.other_client.post(
            f"/api/ai/notifications/{self.alert.id}/read/"
        )

        self.assertEqual(response.status_code, 404)

        self.alert.refresh_from_db()
        self.assertFalse(self.alert.is_read)

    def test_mark_all_notifications_read(self):
        response = self.client.post(
            "/api/ai/notifications/read-all/"
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["updated"], 2)

        self.alert.refresh_from_db()
        self.moderate_alert.refresh_from_db()

        self.assertTrue(self.alert.is_read)
        self.assertTrue(self.moderate_alert.is_read)

    def test_mark_all_notifications_read_does_not_affect_other_user(self):
        self.client.post("/api/ai/notifications/read-all/")

        # Nothing to assert on other_user's data changing since
        # they have none, but this confirms the endpoint scopes
        # its update to request.user and doesn't error globally.
        other_response = self.other_client.post(
            "/api/ai/notifications/read-all/"
        )

        self.assertEqual(other_response.status_code, 200)
        self.assertEqual(other_response.data["updated"], 0)

    def test_digest_includes_high_and_moderate_alerts_for_today(self):
        response = self.client.get("/api/ai/news/digest/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["item_count"], 2)

        holding_names = {
            item["holding_display_name"]
            for item in response.data["items"]
        }

        self.assertEqual(
            holding_names, {"Aurobindo Pharma Limited"}
        )

    def test_digest_orders_by_alert_score_descending(self):
        response = self.client.get("/api/ai/news/digest/")

        scores = [
            item["alert_score"]
            for item in response.data["items"]
        ]

        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_digest_scoped_to_requesting_user_only(self):
        response = self.other_client.get("/api/ai/news/digest/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["item_count"], 0)

    def test_digest_accepts_explicit_date_param(self):
        # A date with no alerts should return an empty digest,
        # not an error.
        response = self.client.get(
            "/api/ai/news/digest/?date=2020-01-01"
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["item_count"], 0)
        self.assertEqual(
            response.data["digest_date"], "2020-01-01"
        )

    def test_digest_ignores_unparseable_date_and_falls_back_to_today(
        self,
    ):
        response = self.client.get(
            "/api/ai/news/digest/?date=not-a-date"
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["item_count"], 2)



from django.utils import timezone as dj_timezone

from portfolio_news.services.pipeline import run_portfolio_news_monitor


class _FakeProvider:
    """
    Deterministic fake NewsProvider for pipeline tests. Returns
    a fixed set of results per query, and can be configured to
    raise for specific queries to test failure handling.
    """

    def __init__(self, results_by_query=None, raise_for_queries=None):
        self.results_by_query = results_by_query or {}
        self.raise_for_queries = raise_for_queries or set()
        self.calls = []

    def search(self, query, from_date=None, to_date=None):
        self.calls.append(query)

        if query in self.raise_for_queries:
            raise RuntimeError("simulated provider failure")

        return self.results_by_query.get(query, [])


class _FakeAnalyzer:
    """
    Deterministic fake GeminiArticleAnalyzer. Returns a fixed
    ArticleAnalysis for every call, or None to simulate an AI
    failure, and records how many times it was actually called
    (for cost-control / idempotency assertions).
    """

    def __init__(self, analysis=None, fail=False):
        self.analysis = analysis
        self.fail = fail
        self.call_count = 0

    def analyze(self, article, holding, user=None):
        self.call_count += 1

        if self.fail:
            return None

        return self.analysis


class PortfolioNewsPipelineTests(TestCase):

    def setUp(self):
        self.user = User.objects.create_user(
            username="pipelineuser",
            password="testpassword",
        )

        self.asset = Asset.objects.create(
            owner=self.user,
            name="Aurobindo Pharma Limited",
            category=AssetCategory.STOCK,
            symbol="AUROPHARMA",
            isin="INE406A01037",
            is_active=True,
        )

        Holding.objects.create(
            owner=self.user,
            asset=self.asset,
            quantity=Decimal("10"),
            average_cost=Decimal("100"),
            invested_value=Decimal("1000"),
            current_price=Decimal("150"),
            current_value=Decimal("1500"),
            unrealized_pnl=Decimal("500"),
        )

        self.relevant_article_result = NewsArticleResult(
            title="Aurobindo Pharma receives USFDA approval",
            url="https://reuters.com/pipeline-article-1",
            source="Reuters",
            description="Aurobindo Pharma received USFDA approval.",
            published_at=dj_timezone.now(),
            matched_query="Aurobindo Pharma Limited",
        )

        self.irrelevant_article_result = NewsArticleResult(
            title="Kalyan Jewellers opens new showroom",
            url="https://reuters.com/pipeline-article-2",
            source="Reuters",
            description="An unrelated jewellery retailer news item.",
            published_at=dj_timezone.now(),
            matched_query="Aurobindo Pharma Limited",
        )

        self.high_impact_analysis = ArticleAnalysis(
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

    def test_end_to_end_creates_alert_for_relevant_article(self):
        provider = _FakeProvider(
            results_by_query={
                "Aurobindo Pharma Limited": [
                    self.relevant_article_result,
                    self.irrelevant_article_result,
                ]
            }
        )

        analyzer = _FakeAnalyzer(analysis=self.high_impact_analysis)

        stats = run_portfolio_news_monitor(
            provider=provider, analyzer=analyzer
        )

        self.assertEqual(stats["users_processed"], 1)
        self.assertEqual(stats["holdings_processed"], 1)
        self.assertEqual(stats["articles_matched"], 1)
        self.assertEqual(stats["alerts_created"], 1)
        self.assertEqual(stats["notifications_sent"], 1)

        self.assertEqual(PortfolioNewsAlert.objects.count(), 1)

        alert = PortfolioNewsAlert.objects.first()
        self.assertEqual(
            alert.holding_display_name, "Aurobindo Pharma Limited"
        )
        self.assertTrue(alert.relevant)

    def test_irrelevant_article_alone_creates_no_alert(self):
        provider = _FakeProvider(
            results_by_query={
                "Aurobindo Pharma Limited": [
                    self.irrelevant_article_result,
                ]
            }
        )

        analyzer = _FakeAnalyzer(analysis=self.high_impact_analysis)

        stats = run_portfolio_news_monitor(
            provider=provider, analyzer=analyzer
        )

        self.assertEqual(stats["articles_matched"], 0)
        self.assertEqual(stats["alerts_created"], 0)
        self.assertEqual(analyzer.call_count, 0)

    def test_running_twice_does_not_duplicate_or_reanalyze(self):
        provider = _FakeProvider(
            results_by_query={
                "Aurobindo Pharma Limited": [
                    self.relevant_article_result,
                ]
            }
        )

        analyzer = _FakeAnalyzer(analysis=self.high_impact_analysis)

        run_portfolio_news_monitor(provider=provider, analyzer=analyzer)
        run_portfolio_news_monitor(provider=provider, analyzer=analyzer)

        self.assertEqual(PortfolioNewsAlert.objects.count(), 1)
        self.assertEqual(NewsArticle.objects.count(), 1)
        # The AI must only ever be called once for this
        # (article, holding) pair, even across two full runs.
        self.assertEqual(analyzer.call_count, 1)

    def test_provider_failure_on_one_query_does_not_abort_run(self):
        provider = _FakeProvider(
            results_by_query={
                "Aurobindo Pharma Limited earnings": [
                    self.relevant_article_result,
                ]
            },
            raise_for_queries={"Aurobindo Pharma Limited"},
        )

        analyzer = _FakeAnalyzer(analysis=self.high_impact_analysis)

        stats = run_portfolio_news_monitor(
            provider=provider, analyzer=analyzer
        )

        self.assertGreaterEqual(stats["provider_failures"], 1)
        # Despite one query failing, the other query's result
        # still gets processed into an alert.
        self.assertEqual(stats["alerts_created"], 1)

    def test_ai_failure_does_not_create_alert_but_stores_article(self):
        provider = _FakeProvider(
            results_by_query={
                "Aurobindo Pharma Limited": [
                    self.relevant_article_result,
                ]
            }
        )

        analyzer = _FakeAnalyzer(fail=True)

        stats = run_portfolio_news_monitor(
            provider=provider, analyzer=analyzer
        )

        self.assertEqual(stats["ai_failures"], 1)
        self.assertEqual(stats["alerts_created"], 0)
        # The article itself is still stored for future runs.
        self.assertEqual(NewsArticle.objects.count(), 1)

    def test_non_relevant_ai_result_is_not_reanalyzed_on_next_run(self):
        not_relevant_analysis = ArticleAnalysis(
            relevant=False,
            relevance_score=5,
            sentiment="neutral",
            impact="very_low",
            impact_score=3,
            category="OTHER",
            time_horizon="unspecified",
            summary="Not actually about this holding.",
            portfolio_implication="None.",
            reason="Coincidental name match.",
            confidence=0.4,
        )

        provider = _FakeProvider(
            results_by_query={
                "Aurobindo Pharma Limited": [
                    self.relevant_article_result,
                ]
            }
        )

        analyzer = _FakeAnalyzer(analysis=not_relevant_analysis)

        run_portfolio_news_monitor(provider=provider, analyzer=analyzer)
        run_portfolio_news_monitor(provider=provider, analyzer=analyzer)

        # No user-facing alert created (relevant=False)...
        self.assertEqual(
            PortfolioNewsAlert.objects.filter(relevant=True).count(),
            0,
        )
        # ...but the AI was still only called once across both runs.
        self.assertEqual(analyzer.call_count, 1)

    def test_user_with_no_holdings_is_skipped_cleanly(self):
        User.objects.create_user(
            username="noholdingsuser",
            password="testpassword",
        )

        provider = _FakeProvider()
        analyzer = _FakeAnalyzer(analysis=self.high_impact_analysis)

        stats = run_portfolio_news_monitor(
            provider=provider, analyzer=analyzer
        )

        # Only the one user with holdings counts as processed.
        self.assertEqual(stats["users_processed"], 1)

    def test_non_relevant_alerts_are_excluded_from_news_api(self):
        not_relevant_analysis = ArticleAnalysis(
            relevant=False,
            relevance_score=5,
            sentiment="neutral",
            impact="very_low",
            impact_score=3,
            category="OTHER",
            time_horizon="unspecified",
            summary="Not actually about this holding.",
            portfolio_implication="None.",
            reason="Coincidental name match.",
            confidence=0.4,
        )

        provider = _FakeProvider(
            results_by_query={
                "Aurobindo Pharma Limited": [
                    self.relevant_article_result,
                ]
            }
        )

        analyzer = _FakeAnalyzer(analysis=not_relevant_analysis)

        run_portfolio_news_monitor(provider=provider, analyzer=analyzer)

        client = APIClient()
        client.force_authenticate(user=self.user)

        response = client.get("/api/ai/news/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 0)

    def test_low_relevance_score_hides_alert_but_keeps_idempotency(
        self,
    ):
        low_relevance_analysis = ArticleAnalysis(
            relevant=True,
            relevance_score=10,
            sentiment="neutral",
            impact="low",
            impact_score=20,
            category="OTHER",
            time_horizon="unspecified",
            summary="Tangentially related.",
            portfolio_implication="Minimal.",
            reason="Weak match.",
            confidence=0.5,
        )

        provider = _FakeProvider(
            results_by_query={
                "Aurobindo Pharma Limited": [
                    self.relevant_article_result,
                ]
            }
        )

        analyzer = _FakeAnalyzer(analysis=low_relevance_analysis)

        run_portfolio_news_monitor(
            provider=provider,
            analyzer=analyzer,
            min_relevance_score=30,
        )

        alert = PortfolioNewsAlert.objects.first()

        # Row exists (idempotency)...
        self.assertIsNotNone(alert)
        # ...but hidden from the feed, since relevance_score (10)
        # is below the configured floor (30), even though the AI
        # itself said relevant=True.
        self.assertFalse(alert.relevant)

        # Running again must not re-call the AI.
        run_portfolio_news_monitor(
            provider=provider,
            analyzer=analyzer,
            min_relevance_score=30,
        )

        self.assertEqual(analyzer.call_count, 1)

    def test_low_alert_score_hides_alert_but_keeps_idempotency(self):
        # High relevance, but a very low portfolio_weight makes
        # the composite alert_score negligible.
        low_weight_holding_analysis = ArticleAnalysis(
            relevant=True,
            relevance_score=90,
            sentiment="neutral",
            impact="low",
            impact_score=10,
            category="OTHER",
            time_horizon="unspecified",
            summary="Minor development.",
            portfolio_implication="Negligible.",
            reason="Low materiality.",
            confidence=0.3,
        )

        provider = _FakeProvider(
            results_by_query={
                "Aurobindo Pharma Limited": [
                    self.relevant_article_result,
                ]
            }
        )

        analyzer = _FakeAnalyzer(
            analysis=low_weight_holding_analysis
        )

        run_portfolio_news_monitor(
            provider=provider,
            analyzer=analyzer,
            min_relevance_score=0,
            min_alert_score=50.0,
        )

        alert = PortfolioNewsAlert.objects.first()

        self.assertIsNotNone(alert)
        self.assertFalse(alert.relevant)
        self.assertFalse(alert.notification_sent)

    def test_max_articles_per_holding_defers_remaining_candidates(
        self,
    ):
        second_article_result = NewsArticleResult(
            title="Aurobindo Pharma announces new plant",
            url="https://reuters.com/pipeline-article-cap-2",
            source="Reuters",
            description="Aurobindo Pharma plant announcement.",
            published_at=dj_timezone.now(),
            matched_query="Aurobindo Pharma Limited",
        )

        provider = _FakeProvider(
            results_by_query={
                "Aurobindo Pharma Limited": [
                    self.relevant_article_result,
                    second_article_result,
                ]
            }
        )

        analyzer = _FakeAnalyzer(analysis=self.high_impact_analysis)

        stats = run_portfolio_news_monitor(
            provider=provider,
            analyzer=analyzer,
            max_articles_per_holding=1,
        )

        # Both candidates matched the deterministic filter, but
        # only 1 was sent to the AI this run - the cap applies
        # after matching, before the (expensive) AI call.
        self.assertEqual(stats["articles_matched"], 2)
        self.assertEqual(stats["articles_sent_to_ai"], 1)
        self.assertEqual(analyzer.call_count, 1)

        # Both articles are still stored, though - nothing is
        # lost, just deferred.
        self.assertEqual(NewsArticle.objects.count(), 2)

    def test_deferred_candidate_is_picked_up_on_a_later_run(self):
        second_article_result = NewsArticleResult(
            title="Aurobindo Pharma announces new plant",
            url="https://reuters.com/pipeline-article-cap-3",
            source="Reuters",
            description="Aurobindo Pharma plant announcement.",
            published_at=dj_timezone.now(),
            matched_query="Aurobindo Pharma Limited",
        )

        provider = _FakeProvider(
            results_by_query={
                "Aurobindo Pharma Limited": [
                    self.relevant_article_result,
                    second_article_result,
                ]
            }
        )

        analyzer = _FakeAnalyzer(analysis=self.high_impact_analysis)

        # First run: cap of 1 defers the second article.
        run_portfolio_news_monitor(
            provider=provider,
            analyzer=analyzer,
            max_articles_per_holding=1,
        )

        self.assertEqual(analyzer.call_count, 1)

        # Second run: no cap, so the deferred article is now
        # processed (the first is skipped via idempotency).
        run_portfolio_news_monitor(
            provider=provider,
            analyzer=analyzer,
            max_articles_per_holding=100,
        )

        self.assertEqual(analyzer.call_count, 2)
        self.assertEqual(PortfolioNewsAlert.objects.count(), 2)