"""Optional IndianAPI integration for Indian mutual-fund research data.

AMFI remains the keyless source of truth for scheme identity and NAV history.
IndianAPI is an optional enrichment source and is never required for PWMS startup,
authentication, portfolio features, or the existing AMFI workflows.
"""
from decimal import Decimal, InvalidOperation
import requests
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from watchlist.models import IndianAPIRequestUsage


class IndianAPIError(Exception):
    """Raised when the optional IndianAPI provider cannot return valid data."""


class IndianAPIConfigurationError(IndianAPIError):
    """Raised when an IndianAPI operation is requested without an API key."""


class IndianAPIQuotaExceeded(IndianAPIError):
    """Raised when PWMS reaches its configured monthly request budget."""

    def __init__(self, month, used, limit):
        self.month = month
        self.used = used
        self.limit = limit
        super().__init__(
            f"PWMS IndianAPI monthly budget exhausted for {month}: "
            f"{used}/{limit} requests used. No provider request was sent. "
            "Wait for the next calendar month or deliberately adjust "
            "INDIANAPI_MONTHLY_REQUEST_BUDGET for the applicable plan."
        )


class IndianAPIRateLimitError(IndianAPIError):
    """Raised when the provider refuses a request due to quota or rate limits."""

    def __init__(self, message, *, retry_after=None, remaining=None, reset=None, provider_message=None):
        super().__init__(message)
        self.retry_after = retry_after
        self.remaining = remaining
        self.reset = reset
        self.provider_message = provider_message


def _number(value):
    if value in (None, ""):
        return None
    try:
        cleaned = str(value).replace(",", "").replace("%", "").strip()
        return float(Decimal(cleaned))
    except (InvalidOperation, TypeError, ValueError):
        return None


def normalize_mutual_fund_payload(payload):
    """Flatten the documented asset-class/category response into fund rows."""
    if not isinstance(payload, dict):
        raise IndianAPIError("IndianAPI mutual-funds response must be a JSON object.")
    data = payload.get("data", payload)
    if not isinstance(data, dict):
        raise IndianAPIError("IndianAPI mutual-funds response has an unsupported shape.")

    rows = []
    for asset_class, categories in data.items():
        if not isinstance(categories, dict):
            continue
        for category, funds in categories.items():
            if not isinstance(funds, list):
                continue
            for item in funds:
                if not isinstance(item, dict):
                    continue
                name = item.get("fund_name") or item.get("schemeName") or item.get("scheme_name")
                if not name:
                    continue
                rows.append({
                    "fund_name": str(name).strip(),
                    "asset_class": str(asset_class),
                    "category": str(category),
                    "latest_nav": _number(item.get("latest_nav") or item.get("nav")),
                    "percentage_change": _number(item.get("percentage_change")),
                    "asset_size": _number(item.get("asset_size") or item.get("aum")),
                    "returns": {
                        "1M": _number(item.get("1_month_return")),
                        "3M": _number(item.get("3_month_return")),
                        "6M": _number(item.get("6_month_return")),
                        "1Y": _number(item.get("1_year_return")),
                        "3Y": _number(item.get("3_year_return")),
                        "5Y": _number(item.get("5_year_return")),
                    },
                    "star_rating": _number(item.get("star_rating")),
                })
    return rows


class IndianAPIClient:
    """Server-side client for the configured IndianAPI plan's mutual-fund endpoint."""

    def __init__(self):
        self.api_key = str(getattr(settings, "INDIANAPI_API_KEY", "") or "").strip()
        self.base_url = str(
            getattr(settings, "INDIANAPI_BASE_URL", "https://stock.indianapi.in") or ""
        ).strip().rstrip("/")
        self.timeout = max(1, int(getattr(settings, "INDIANAPI_TIMEOUT_SECONDS", 20)))
        self.monthly_budget = max(1, int(getattr(settings, "INDIANAPI_MONTHLY_REQUEST_BUDGET", 450)))
        self.last_request_usage = None

    @property
    def is_configured(self):
        return bool(self.api_key)

    def _reserve_monthly_request(self):
        """Reserve one request before network I/O; failed attempts count conservatively."""
        now = timezone.now()
        month = timezone.localdate(now).strftime("%Y-%m")
        with transaction.atomic():
            usage, _ = IndianAPIRequestUsage.objects.get_or_create(month=month)
            usage = IndianAPIRequestUsage.objects.select_for_update().get(pk=usage.pk)
            if usage.request_count >= self.monthly_budget:
                raise IndianAPIQuotaExceeded(month, usage.request_count, self.monthly_budget)
            usage.request_count += 1
            usage.last_requested_at = now
            usage.save(update_fields=["request_count", "last_requested_at", "updated_at"])
            self.last_request_usage = {
                "month": month,
                "used": usage.request_count,
                "limit": self.monthly_budget,
                "remaining": max(0, self.monthly_budget - usage.request_count),
            }
        return self.last_request_usage

    def fetch_mutual_funds(self):
        if not self.is_configured:
            raise IndianAPIConfigurationError(
                "IndianAPI is not configured. Set INDIANAPI_API_KEY to enable optional enrichment."
            )
        if not self.base_url:
            raise IndianAPIConfigurationError("INDIANAPI_BASE_URL is empty.")
        # Reserve before sending. This also counts timeouts/connection failures,
        # because the provider may have received and charged for the request.
        self._reserve_monthly_request()
        try:
            response = requests.get(
                f"{self.base_url}/mutual_funds",
                headers={"X-Api-Key": self.api_key, "Accept": "application/json"},
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise IndianAPIError(f"IndianAPI connection failed: {exc}") from exc

        if response.status_code == 429:
            # Do not automatically retry: provider documentation says 429 can
            # mean exhausted credits or a plan rate limit, and retries may
            # consume additional quota. Preserve any previously stored snapshot.
            try:
                error_payload = response.json()
            except ValueError:
                error_payload = {}
            provider_message = ""
            if isinstance(error_payload, dict):
                provider_message = (
                    error_payload.get("message")
                    or error_payload.get("detail")
                    or error_payload.get("error")
                    or ""
                )
            headers = response.headers or {}
            retry_after = headers.get("Retry-After")
            remaining = headers.get("X-RateLimit-Remaining")
            reset = headers.get("X-RateLimit-Reset")
            details = ["IndianAPI returned HTTP 429 (rate limit or API credits exhausted)."]
            if provider_message:
                details.append(f"Provider: {provider_message}")
            if remaining is not None:
                details.append(f"Credits/requests remaining header: {remaining}.")
            if retry_after:
                details.append(f"Retry-After: {retry_after} seconds.")
            if reset:
                details.append(f"Rate-limit reset header: {reset}.")
            details.append(
                "Check your IndianAPI dashboard for remaining credits and plan limits. "
                "No automatic retry was attempted; the previous PWMS snapshot, if any, is unchanged."
            )
            raise IndianAPIRateLimitError(
                " ".join(details),
                retry_after=retry_after,
                remaining=remaining,
                reset=reset,
                provider_message=str(provider_message) if provider_message else None,
            )

        try:
            response.raise_for_status()
        except requests.RequestException as exc:
            raise IndianAPIError(f"IndianAPI request failed: {exc}") from exc
        try:
            payload = response.json()
        except ValueError as exc:
            raise IndianAPIError("IndianAPI returned a non-JSON response.") from exc
        if isinstance(payload, dict) and payload.get("error"):
            raise IndianAPIError(str(payload["error"]))
        normalize_mutual_fund_payload(payload)
        return payload
