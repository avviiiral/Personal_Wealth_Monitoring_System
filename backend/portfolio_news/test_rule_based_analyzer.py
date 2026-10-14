from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from portfolio_news.constants import ImpactLevel, NewsCategory, Sentiment
from config.pwms_config import CONFIG
from portfolio_news.services.holding_matcher import HoldingMatcher
from portfolio_news.services.holdings_registry import MonitoredHolding
from portfolio_news.services.rule_based_analyzer import RuleBasedArticleAnalyzer


class RuleBasedArticleAnalyzerTests(SimpleTestCase):
    def setUp(self):
        self.analyzer = RuleBasedArticleAnalyzer()
        self.holding = SimpleNamespace(
            holding_type="EQUITY",
            holding_id=1,
            display_name="ABC Bank",
            symbol="ABCBANK",
            amc_name="",
            portfolio_weight=25.0,
        )

    def article(self, title, description="", source="Reuters", query="ABC Bank"):
        return SimpleNamespace(
            id=1,
            title=title,
            description=description,
            source=source,
            source_quality="tier_1",
            matched_query=query,
            published_at=None,
        )

    def test_regulatory_negative_story_is_classified_without_ai(self):
        result = self.analyzer.analyze(
            self.article(
                "SEBI penalty and regulatory action against ABC Bank",
                "The regulator announced a major penalty.",
            ),
            self.holding,
        )

        self.assertEqual(result.category, NewsCategory.REGULATORY)
        self.assertEqual(result.sentiment, Sentiment.NEGATIVE)
        self.assertIn(result.impact, {
            ImpactLevel.HIGH,
            ImpactLevel.CRITICAL,
        })
        self.assertGreaterEqual(result.relevance_score, 70)
        self.assertTrue(result.relevant)

    def test_critical_risk_event_reaches_critical_impact(self):
        result = self.analyzer.analyze(
            self.article(
                "ABC Bank faces accounting fraud and debt default",
                "Regulatory action follows an accounting irregularity.",
            ),
            self.holding,
        )

        self.assertEqual(result.impact, ImpactLevel.CRITICAL)
        self.assertGreaterEqual(result.impact_score, 81)
        self.assertTrue(result.relevant)

    def test_major_business_event_reaches_high_impact(self):
        result = self.analyzer.analyze(
            self.article(
                "ABC Bank CEO resigns after major investigation",
                "The company also withdrew its guidance.",
            ),
            self.holding,
        )

        self.assertEqual(result.impact, ImpactLevel.HIGH)
        self.assertGreaterEqual(result.impact_score, 61)
        self.assertTrue(result.relevant)

    def test_neutral_sector_story_stays_conservative(self):
        result = self.analyzer.analyze(
            self.article(
                "Indian banking sector outlook",
                "Analysts discuss sector demand and interest rates.",
                source="Google News",
                query="Indian bank sector",
            ),
            self.holding,
        )

        self.assertEqual(result.sentiment, Sentiment.NEUTRAL)
        self.assertEqual(result.category, NewsCategory.INDUSTRY)
        self.assertLess(result.impact_score, 61)
        self.assertGreaterEqual(result.confidence, 0.35)

    def test_batch_returns_existing_pipeline_key_shape(self):
        article = self.article("ABC Bank reports strong profit growth")
        results = self.analyzer.analyze_batch([(article, self.holding)])

        self.assertIn((1, "EQUITY", 1), results)
        self.assertEqual(results[(1, "EQUITY", 1)].category, NewsCategory.EARNINGS)


class RuleBasedAnalyzerAccuracyTests(SimpleTestCase):
    """Word-boundary matching, negation, relevance gating and explanations."""

    def setUp(self):
        self.analyzer = RuleBasedArticleAnalyzer()
        self.holding = SimpleNamespace(
            holding_type="EQUITY",
            holding_id=1,
            display_name="ABC Bank",
            symbol="ABCBANK",
            amc_name="",
            aliases=["ABC"],
            isin="",
            portfolio_weight=12.5,
            current_value=1234567,
            underlyings=[],
        )

    def article(self, title, description="", query="ABC Bank", source="Reuters"):
        return SimpleNamespace(
            id=1,
            title=title,
            description=description,
            source=source,
            source_quality="tier_1",
            matched_query=query,
            published_at=None,
        )

    def test_keywords_do_not_match_inside_other_words(self):
        result = self.analyzer.analyze(
            self.article(
                "ABC Bank refinery financing steps announced",
                "Loan officers outline next steps.",
            ),
            self.holding,
        )

        self.assertEqual(result.sentiment, Sentiment.NEUTRAL)
        self.assertNotEqual(result.category, NewsCategory.EARNINGS)
        self.assertNotEqual(result.category, NewsCategory.LEGAL)

    def test_inflected_keywords_still_match(self):
        result = self.analyzer.analyze(
            self.article("ABC Bank fined by regulator", "The bank was fined for lapses."),
            self.holding,
        )

        self.assertEqual(result.sentiment, Sentiment.NEGATIVE)

    def test_plain_regulatory_penalty_is_at_least_moderate_impact(self):
        result = self.analyzer.analyze(
            self.article("ABC Bank faces RBI penalty over compliance lapses"),
            self.holding,
        )

        self.assertEqual(result.category, NewsCategory.REGULATORY)
        self.assertGreaterEqual(result.impact_score, 36)

    def test_negated_risk_words_are_ignored(self):
        result = self.analyzer.analyze(
            self.article("ABC Bank denies fraud claims", "No penalty has been imposed."),
            self.holding,
        )

        self.assertEqual(result.sentiment, Sentiment.NEUTRAL)
        self.assertLess(result.impact_score, 41)

    def test_article_not_naming_holding_is_not_relevant(self):
        result = self.analyzer.analyze(
            self.article(
                "Stock markets close higher on Friday",
                "Benchmark indices gained as investors cheered earnings.",
            ),
            self.holding,
        )

        self.assertFalse(result.relevant)
        self.assertLess(result.relevance_score, 50)

    def test_sector_story_hidden_by_default_and_shown_when_enabled(self):
        article = self.article(
            "Indian banking sector outlook",
            "Analysts discuss sector demand and interest rates.",
            query="Indian Banking sector",
        )

        self.assertFalse(self.analyzer.analyze(article, self.holding).relevant)

        with patch.dict(CONFIG["news"], {"allow_sector_context": True}):
            result = self.analyzer.analyze(article, self.holding)

        self.assertEqual(result.relevance_score, 50)
        self.assertIn("indirect", result.reason)

    def test_direct_match_explanation_uses_portfolio_numbers(self):
        result = self.analyzer.analyze(
            self.article("ABC Bank reports record profit", "Quarterly results beat estimates."),
            self.holding,
        )

        self.assertTrue(result.relevant)
        self.assertIn("names ABC Bank directly", result.reason)
        self.assertIn("12.5% of your portfolio", result.portfolio_implication)
        self.assertIn("\u20b912,34,567", result.portfolio_implication)
        self.assertIn("large position", result.portfolio_implication)
        self.assertIn("not a prediction", result.interpretation)

    def test_underlying_match_is_labelled_indirect_and_estimated(self):
        fund = SimpleNamespace(
            holding_type="MUTUAL_FUND",
            holding_id=7,
            display_name="Growth Fund",
            symbol="",
            amc_name="",
            aliases=[],
            isin="",
            portfolio_weight=5.0,
            current_value=100000,
            underlyings=[SimpleNamespace(name="XYZ Industries", isin="", weight=8.0)],
        )

        result = self.analyzer.analyze(
            self.article("XYZ Industries wins large order", query="XYZ Industries"),
            fund,
        )

        self.assertTrue(result.relevant)
        self.assertIn("indirect link", result.reason)
        self.assertIn("8.00% of Growth Fund", result.portfolio_implication)
        self.assertIn("(estimate)", result.portfolio_implication)
        self.assertIn("\u20b98,000", result.portfolio_implication)


class SectorMatchGateTests(SimpleTestCase):
    def setUp(self):
        self.holding = MonitoredHolding(
            holding_type="EQUITY",
            holding_id=1,
            display_name="ABC Bank",
            sector="Banking",
        )
        self.args = (
            "Indian banking sector outlook",
            "Analysts discuss banking demand.",
            self.holding,
            "Indian Banking sector",
        )

    def test_sector_only_story_does_not_match_by_default(self):
        self.assertEqual(HoldingMatcher.match_score(*self.args), 0)
        self.assertFalse(HoldingMatcher.is_relevant(*self.args))

    def test_sector_only_story_matches_when_context_enabled(self):
        with patch.dict(CONFIG["news"], {"allow_sector_context": True}):
            self.assertEqual(HoldingMatcher.match_score(*self.args), 50)

    def test_direct_company_story_still_matches(self):
        score = HoldingMatcher.match_score(
            "ABC Bank posts higher profit", "", self.holding, "ABC Bank"
        )

        self.assertEqual(score, 100)
