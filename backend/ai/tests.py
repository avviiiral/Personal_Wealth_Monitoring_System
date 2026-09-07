from datetime import datetime, timezone as dt_timezone
from unittest.mock import MagicMock, patch

from django.contrib.auth.models import User
from django.test import TestCase

from rest_framework.test import APIClient

from portfolio_news.services.article_store import store_article
from portfolio_news.services.holdings_registry import (
    HoldingType,
    MonitoredHolding,
)
from portfolio_news.services.gemini_analyzer import ArticleAnalysis
from portfolio_news.services.news_provider import NewsArticleResult
from portfolio_news.services.notification_creation import (
    create_alert_from_analysis,
)

from ai.services.portfolio_news_context import (
    PortfolioNewsChatContextBuilder,
)


class PortfolioNewsChatContextBuilderTests(TestCase):

    def setUp(self):
        self.user = User.objects.create_user(
            username="chatuser",
            password="testpassword",
        )

        self.other_user = User.objects.create_user(
            username="otherchatuser",
            password="testpassword",
        )

        self.holding = MonitoredHolding(
            holding_type=HoldingType.EQUITY,
            holding_id=201,
            display_name="Aurobindo Pharma Limited",
            symbol="AUROPHARMA",
            portfolio_weight=18.4,
        )

    def _analysis(self, impact, impact_score, confidence=0.9):
        return ArticleAnalysis(
            relevant=True,
            relevance_score=90,
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

    def test_empty_when_no_alerts_exist(self):
        context = PortfolioNewsChatContextBuilder.build(self.user)

        self.assertEqual(context["total_alerts_in_window"], 0)
        self.assertEqual(context["alerts"], [])

    def test_includes_alert_within_lookback_window(self):
        article, _ = store_article(
            NewsArticleResult(
                title="Aurobindo Pharma receives USFDA approval",
                url="https://reuters.com/aurobindo-chat-1",
                source="Reuters",
                description="USFDA approval news.",
                published_at=datetime(
                    2026, 8, 24, 9, 0, tzinfo=dt_timezone.utc
                ),
            )
        )

        create_alert_from_analysis(
            self.user,
            article,
            self.holding,
            self._analysis(impact="high", impact_score=80),
        )

        context = PortfolioNewsChatContextBuilder.build(self.user)

        self.assertEqual(context["total_alerts_in_window"], 1)
        self.assertEqual(len(context["alerts"]), 1)
        self.assertEqual(
            context["alerts"][0]["holding"],
            "Aurobindo Pharma Limited",
        )
        self.assertIn("key_facts", context["alerts"][0])
        self.assertIn(
            "uncertainty_notes", context["alerts"][0]
        )

    def test_excludes_other_users_alerts(self):
        article, _ = store_article(
            NewsArticleResult(
                title="Aurobindo Pharma receives USFDA approval",
                url="https://reuters.com/aurobindo-chat-2",
                source="Reuters",
                description="USFDA approval news.",
            )
        )

        create_alert_from_analysis(
            self.other_user,
            article,
            self.holding,
            self._analysis(impact="high", impact_score=80),
        )

        context = PortfolioNewsChatContextBuilder.build(self.user)

        self.assertEqual(context["total_alerts_in_window"], 0)

    def test_respects_max_alerts_cap(self):
        for i in range(5):
            article, _ = store_article(
                NewsArticleResult(
                    title=f"Aurobindo Pharma news item {i}",
                    url=f"https://reuters.com/aurobindo-chat-cap-{i}",
                    source="Reuters",
                    description="News.",
                )
            )

            create_alert_from_analysis(
                self.user,
                article,
                self.holding,
                self._analysis(
                    impact="moderate", impact_score=50
                ),
            )

        context = PortfolioNewsChatContextBuilder.build(
            self.user, max_alerts=2
        )

        self.assertEqual(context["total_alerts_in_window"], 5)
        self.assertEqual(len(context["alerts"]), 2)

    def test_excludes_alerts_outside_lookback_window(self):
        from datetime import timedelta

        from django.utils import timezone as dj_timezone

        article, _ = store_article(
            NewsArticleResult(
                title="Aurobindo Pharma old news",
                url="https://reuters.com/aurobindo-chat-old-1",
                source="Reuters",
                description="Old news.",
            )
        )

        alert, _ = create_alert_from_analysis(
            self.user,
            article,
            self.holding,
            self._analysis(impact="high", impact_score=80),
        )

        from portfolio_news.models import PortfolioNewsAlert

        PortfolioNewsAlert.objects.filter(id=alert.id).update(
            created_at=dj_timezone.now() - timedelta(days=90)
        )

        context = PortfolioNewsChatContextBuilder.build(
            self.user, lookback_days=30
        )

        self.assertEqual(context["total_alerts_in_window"], 0)


class PortfolioChatNewsIntegrationTests(TestCase):
    """
    Covers portfolio_chat's use of PortfolioNewsChatContextBuilder
    - specifically that news context is included in what's sent
    to Gemini, and that a failure building it degrades gracefully
    rather than failing the whole chat request.
    """

    def setUp(self):
        self.user = User.objects.create_user(
            username="chatviewuser",
            password="testpassword",
        )

        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def _mock_gemini_response(self, text="Here is your answer."):
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "candidates": [
                {
                    "content": {
                        "parts": [{"text": text}],
                    }
                }
            ]
        }
        return mock_response

    def test_news_context_is_included_in_gemini_payload(self):
        with patch(
            "ai.views.get_gemini_api_key",
            return_value="fake-key",
        ):
            with patch(
                "ai.views.PortfolioContextBuilder.build",
                return_value={"user": {"id": self.user.id}},
            ):
                with patch(
                    "ai.views.requests.post"
                ) as mock_post:
                    mock_post.return_value = (
                        self._mock_gemini_response()
                    )

                    response = self.client.post(
                        "/api/ai/chat/",
                        {"message": "What news affects my portfolio?"},
                        format="json",
                    )

        self.assertEqual(response.status_code, 200)

        sent_payload = mock_post.call_args.kwargs["json"]

        user_content = sent_payload["contents"][0]["parts"][0]["text"]

        self.assertIn('"news"', user_content)
        self.assertIn("total_alerts_in_window", user_content)

    def test_news_context_failure_does_not_break_chat(self):
        with patch(
            "ai.views.get_gemini_api_key",
            return_value="fake-key",
        ):
            with patch(
                "ai.views.PortfolioContextBuilder.build",
                return_value={"user": {"id": self.user.id}},
            ):
                with patch(
                    "ai.views.PortfolioNewsChatContextBuilder.build",
                    side_effect=Exception("db unavailable"),
                ):
                    with patch(
                        "ai.views.requests.post"
                    ) as mock_post:
                        mock_post.return_value = (
                            self._mock_gemini_response()
                        )

                        response = self.client.post(
                            "/api/ai/chat/",
                            {"message": "Hello"},
                            format="json",
                        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("answer", response.data)

        sent_payload = mock_post.call_args.kwargs["json"]

        user_content = sent_payload["contents"][0]["parts"][0]["text"]

        self.assertIn(
            "could not be loaded", user_content
        )


class GeminiUsageTrackingTests(TestCase):
    """
    Covers ai/services/usage_tracking.py and its wiring into
    portfolio_chat - both that a successful call is recorded,
    and that a recording failure never breaks the actual
    response (the API call already happened/was billed by the
    time recording runs, so losing the record must never mean
    losing the answer).
    """

    def setUp(self):
        self.user = User.objects.create_user(
            username="usageuser",
            password="testpassword",
        )

        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def _mock_gemini_response(self, text="Here is your answer."):
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "candidates": [
                {
                    "content": {
                        "parts": [{"text": text}],
                    }
                }
            ],
            "usageMetadata": {
                "promptTokenCount": 500,
                "candidatesTokenCount": 50,
                "totalTokenCount": 550,
                "cachedContentTokenCount": 0,
            },
        }
        return mock_response

    def test_successful_chat_call_records_usage(self):
        from ai.models import GeminiUsageLog

        with patch(
            "ai.views.get_gemini_api_key",
            return_value="fake-key",
        ):
            with patch(
                "ai.views.PortfolioContextBuilder.build",
                return_value={"user": {"id": self.user.id}},
            ):
                with patch(
                    "ai.views.requests.post"
                ) as mock_post:
                    mock_post.return_value = (
                        self._mock_gemini_response()
                    )

                    response = self.client.post(
                        "/api/ai/chat/",
                        {"message": "What's my portfolio worth?"},
                        format="json",
                    )

        self.assertEqual(response.status_code, 200)

        log = GeminiUsageLog.objects.filter(
            endpoint="portfolio_chat"
        ).first()

        self.assertIsNotNone(log)
        self.assertEqual(log.user, self.user)
        self.assertEqual(log.prompt_tokens, 500)
        self.assertEqual(log.output_tokens, 50)
        self.assertEqual(log.total_tokens, 550)

    def test_usage_recording_failure_does_not_break_chat_response(
        self,
    ):
        with patch(
            "ai.views.get_gemini_api_key",
            return_value="fake-key",
        ):
            with patch(
                "ai.views.PortfolioContextBuilder.build",
                return_value={"user": {"id": self.user.id}},
            ):
                with patch(
                    "ai.views.requests.post"
                ) as mock_post:
                    mock_post.return_value = (
                        self._mock_gemini_response()
                    )

                    with patch(
                        "ai.views.record_gemini_usage",
                        side_effect=Exception("db down"),
                    ):
                        response = self.client.post(
                            "/api/ai/chat/",
                            {"message": "Hello"},
                            format="json",
                        )

        # The chat answer must still succeed even though usage
        # recording itself raised.
        self.assertEqual(response.status_code, 200)
        self.assertIn("answer", response.data)

    def test_record_gemini_usage_handles_missing_fields_safely(self):
        from ai.models import GeminiUsageLog
        from ai.services.usage_tracking import record_gemini_usage

        record_gemini_usage(
            user=self.user,
            endpoint="article_analysis",
            model_name="gemini-3.6-flash",
            usage_metadata={},
        )

        log = GeminiUsageLog.objects.filter(
            endpoint="article_analysis"
        ).first()

        self.assertIsNotNone(log)
        self.assertEqual(log.prompt_tokens, 0)
        self.assertEqual(log.total_tokens, 0)

    def test_record_gemini_usage_never_raises_on_db_failure(self):
        from ai.services.usage_tracking import record_gemini_usage

        with patch(
            "ai.models.GeminiUsageLog.objects.create",
            side_effect=Exception("simulated db failure"),
        ):
            # Must not raise.
            record_gemini_usage(
                user=self.user,
                endpoint="portfolio_chat",
                model_name="gemini-3.6-flash",
                usage_metadata={"totalTokenCount": 100},
            )

    def test_article_analysis_records_usage_with_user(self):
        from ai.models import GeminiUsageLog
        from portfolio_news.services.gemini_analyzer import (
            GeminiArticleAnalyzer,
        )
        from portfolio_news.services.holdings_registry import (
            HoldingType,
            MonitoredHolding,
        )

        holding = MonitoredHolding(
            holding_type=HoldingType.EQUITY,
            holding_id=1,
            display_name="Aurobindo Pharma Limited",
            symbol="AUROPHARMA",
        )

        class _FakeArticle:
            id = 1
            title = "Aurobindo Pharma receives USFDA approval"
            description = "Approval news."
            source = "Reuters"
            published_at = None

        analyzer = GeminiArticleAnalyzer()

        mock_response = self._mock_gemini_response(
            text=(
                '{"relevant": true, "relevance_score": 90, '
                '"sentiment": "positive", "impact": "high", '
                '"impact_score": 80, "category": "REGULATORY", '
                '"time_horizon": "medium_term", "summary": "s", '
                '"portfolio_implication": "p", "reason": "r", '
                '"confidence": 0.9, "materiality": "high", '
                '"key_facts": "f", "interpretation": "i", '
                '"uncertainty_notes": "u"}'
            )
        )

        with patch(
            "portfolio_news.services.gemini_analyzer.get_gemini_api_key",
            return_value="fake-key",
        ):
            with patch(
                "portfolio_news.services.gemini_analyzer.requests.post",
                return_value=mock_response,
            ):
                analyzer.analyze(
                    _FakeArticle(), holding, user=self.user
                )

        log = GeminiUsageLog.objects.filter(
            endpoint="article_analysis"
        ).first()

        self.assertIsNotNone(log)
        self.assertEqual(log.user, self.user)
        self.assertEqual(log.total_tokens, 550)
