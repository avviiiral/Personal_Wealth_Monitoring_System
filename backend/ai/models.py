from django.conf import settings
from django.db import models


class GeminiUsageLog(models.Model):
    """
    One row per real Gemini API call (article analysis or
    portfolio chat), so token usage/cost can actually be
    queried later instead of only existing as scattered log
    lines. Recording a row must never be able to break the
    call it's recording - see services/usage_tracking.py.
    """

    ENDPOINT_ARTICLE_ANALYSIS = "article_analysis"
    ENDPOINT_PORTFOLIO_CHAT = "portfolio_chat"

    ENDPOINT_CHOICES = [
        (ENDPOINT_ARTICLE_ANALYSIS, "Article Analysis"),
        (ENDPOINT_PORTFOLIO_CHAT, "Portfolio Chat"),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="gemini_usage_logs",
        help_text=(
            "Null for system/background calls not tied to a "
            "specific user's request (there currently are none, "
            "but this keeps the model correct if that changes)."
        ),
    )

    endpoint = models.CharField(
        max_length=30,
        choices=ENDPOINT_CHOICES,
    )

    model_name = models.CharField(
        max_length=100,
        blank=True,
    )

    prompt_tokens = models.PositiveIntegerField(default=0)

    output_tokens = models.PositiveIntegerField(default=0)

    total_tokens = models.PositiveIntegerField(default=0)

    cached_tokens = models.PositiveIntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

        indexes = [
            models.Index(
                fields=["endpoint", "-created_at"],
                name="gemini_usage_endpoint_idx",
            ),
            models.Index(
                fields=["user", "-created_at"],
                name="gemini_usage_user_idx",
            ),
        ]

    def __str__(self):
        return (
            f"{self.endpoint}: {self.total_tokens} tokens "
            f"({self.created_at:%Y-%m-%d %H:%M})"
        )
