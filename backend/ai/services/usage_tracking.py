"""
Records Gemini API token usage to GeminiUsageLog.

Deliberately isolated from the actual Gemini call sites: a
failure here (a DB hiccup, a migration not yet applied, etc.)
must never break article analysis or portfolio chat - both are
already-completed, already-billed API calls by the time this
runs, so losing the usage record is far preferable to losing the
answer the user is waiting on.
"""

import logging


logger = logging.getLogger(__name__)


def record_gemini_usage(user, endpoint: str, model_name: str, usage_metadata: dict) -> None:
    """
    Persist one GeminiUsageLog row from a Gemini response's
    `usageMetadata` dict. Never raises.
    """

    try:
        from ..models import GeminiUsageLog

        GeminiUsageLog.objects.create(
            user=user,
            endpoint=endpoint,
            model_name=model_name or "",
            prompt_tokens=usage_metadata.get(
                "promptTokenCount", 0
            ) or 0,
            output_tokens=usage_metadata.get(
                "candidatesTokenCount", 0
            ) or 0,
            total_tokens=usage_metadata.get(
                "totalTokenCount", 0
            ) or 0,
            cached_tokens=usage_metadata.get(
                "cachedContentTokenCount", 0
            ) or 0,
        )

    except Exception:
        logger.exception(
            "Failed to record Gemini usage for endpoint=%r "
            "(the API call itself already completed - this "
            "only affects usage tracking, not the response).",
            endpoint,
        )
