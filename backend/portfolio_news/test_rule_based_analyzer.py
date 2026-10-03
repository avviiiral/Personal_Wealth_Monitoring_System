from types import SimpleNamespace

from django.test import SimpleTestCase

from portfolio_news.constants import ImpactLevel, NewsCategory, Sentiment
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
