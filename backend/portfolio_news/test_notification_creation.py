from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase

from investments.models import Asset, AssetCategory, Holding
from portfolio_news.models import NewsArticle, PortfolioNewsAlert
from portfolio_news.services.notification_creation import create_alert_from_analysis


class NotificationCreationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="notification-user",
            password="test-password",
        )
        self.asset = Asset.objects.create(
            owner=self.user,
            name="Test Company Limited",
            category=AssetCategory.STOCK,
            symbol="TESTCO",
            isin="INE000000000",
            is_active=True,
        )
        self.holding = Holding.objects.create(
            owner=self.user,
            asset=self.asset,
            quantity=Decimal("10"),
            average_cost=Decimal("100"),
            invested_value=Decimal("1000"),
            current_price=Decimal("120"),
            current_value=Decimal("1200"),
            unrealized_pnl=Decimal("200"),
        )
        self.article = NewsArticle.objects.create(
            title="Test Company reports a major filing",
            normalized_title="test company reports a major filing",
            url="https://example.com/test-company-filing",
            url_hash="a" * 64,
            source="Exchange",
            description="Test description",
            fingerprint="b" * 64,
            matched_query="Test Company",
            source_quality="tier_1",
            source_count=1,
        )

    def _analysis(self, impact="high"):
        return type(
            "Analysis",
            (),
            {
                "impact_score": 80 if impact == "high" else 90,
                "confidence": 1.0,
                "impact": impact,
                "relevant": True,
                "category": "REGULATORY",
                "sentiment": "neutral",
                "time_horizon": "short_term",
                "relevance_score": 100,
                "summary": "A test alert.",
                "portfolio_implication": "Test implication.",
                "reason": "Test reason.",
                "materiality": "high",
                "key_facts": "Test facts.",
                "interpretation": "",
                "uncertainty_notes": "",
            },
        )()

    def _holding(self):
        return type(
            "Holding",
            (),
            {
                "holding_type": "EQUITY",
                "holding_id": self.asset.id,
                "display_name": self.asset.name,
                "portfolio_weight": 100.0,
            },
        )()

    def test_new_alert_is_unmarked_until_delivery(self):
        alert, created = create_alert_from_analysis(
            self.user,
            self.article,
            self._holding(),
            self._analysis("high"),
        )

        self.assertTrue(created)
        self.assertFalse(alert.notification_sent)
        self.assertEqual(alert.notification_tier, "high")

    def test_repeated_creation_does_not_duplicate(self):
        first, created_first = create_alert_from_analysis(
            self.user,
            self.article,
            self._holding(),
            self._analysis("critical"),
        )
        second, created_second = create_alert_from_analysis(
            self.user,
            self.article,
            self._holding(),
            self._analysis("critical"),
        )

        self.assertTrue(created_first)
        self.assertFalse(created_second)
        self.assertEqual(first.id, second.id)
        self.assertEqual(PortfolioNewsAlert.objects.count(), 1)
