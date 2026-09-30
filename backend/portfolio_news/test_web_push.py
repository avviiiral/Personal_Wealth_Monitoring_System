from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase, override_settings

from portfolio_news.constants import (
    ImpactLevel,
    NotificationTier,
)
from portfolio_news.models import (
    NewsArticle,
    PortfolioNewsAlert,
    PushSubscription,
)
from portfolio_news.services.web_push import deliver_alert_notification


class WebPushDeliveryTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="push-user",
            password="password",
        )
        self.article = NewsArticle.objects.create(
            title="Material portfolio event",
            normalized_title="material portfolio event",
            url="https://example.com/news/1",
            url_hash="push-test-url-1",
            source="Example",
            description="A material event.",
            fingerprint="push-test-fingerprint-1",
        )
        self.alert = PortfolioNewsAlert.objects.create(
            user=self.user,
            article=self.article,
            holding_type="EQUITY",
            holding_id=1,
            holding_display_name="Example Holdings",
            relevant=True,
            category="OTHER",
            sentiment="neutral",
            time_horizon="short_term",
            relevance_score=90,
            impact=ImpactLevel.HIGH,
            impact_score=80,
            confidence=1.0,
            portfolio_weight_at_alert=10.0,
            alert_score=8.0,
            notification_tier=NotificationTier.HIGH,
            summary="A material event.",
            portfolio_implication="Portfolio implication.",
            reason="High impact.",
            notification_sent=False,
        )

    @override_settings(
        WEB_PUSH_ENABLED=True,
        WEB_PUSH_VAPID_PUBLIC_KEY="public",
        WEB_PUSH_VAPID_PRIVATE_KEY="private",
        WEB_PUSH_VAPID_SUBJECT="mailto:test@example.com",
    )
    @patch("pywebpush.webpush")
    def test_successful_delivery_marks_alert_sent(self, mock_webpush):
        PushSubscription.objects.create(
            user=self.user,
            endpoint="https://push.example.com/subscription/1",
            p256dh="p256dh",
            auth="auth",
        )

        delivered = deliver_alert_notification(self.alert)

        self.assertTrue(delivered)
        mock_webpush.assert_called_once()
        self.alert.refresh_from_db()
        self.assertTrue(self.alert.notification_sent)

    @override_settings(WEB_PUSH_ENABLED=False)
    def test_unconfigured_push_does_not_mark_alert_sent(self):
        delivered = deliver_alert_notification(self.alert)

        self.assertFalse(delivered)
        self.alert.refresh_from_db()
        self.assertFalse(self.alert.notification_sent)

    @override_settings(
        WEB_PUSH_ENABLED=True,
        WEB_PUSH_VAPID_PUBLIC_KEY="public",
        WEB_PUSH_VAPID_PRIVATE_KEY="private",
        WEB_PUSH_VAPID_SUBJECT="mailto:test@example.com",
    )
    @patch("pywebpush.webpush")
    def test_no_subscription_does_not_mark_alert_sent(self, mock_webpush):
        delivered = deliver_alert_notification(self.alert)

        self.assertFalse(delivered)
        mock_webpush.assert_not_called()
        self.alert.refresh_from_db()
        self.assertFalse(self.alert.notification_sent)
