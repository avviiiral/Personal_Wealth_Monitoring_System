"""Optional IndianAPI integration for Indian mutual-fund research data.

AMFI remains the keyless source of truth for scheme identity and NAV history.
IndianAPI is an optional enrichment source and is never required for PWMS startup,
authentication, portfolio features, or the existing AMFI workflows.
"""
from decimal import Decimal, InvalidOperation
import requests
from django.conf import settings


class IndianAPIError(Exception):
    """Raised when the optional IndianAPI provider cannot return valid data."""


class IndianAPIConfigurationError(IndianAPIError):
    """Raised when an IndianAPI operation is requested without an API key."""


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
    """Small server-side client for the IndianAPI Free/Hobby mutual-fund endpoint."""

    def __init__(self):
        self.api_key = str(getattr(settings, "INDIANAPI_API_KEY", "") or "").strip()
        self.base_url = str(
            getattr(settings, "INDIANAPI_BASE_URL", "https://stock.indianapi.in") or ""
        ).strip().rstrip("/")
        self.timeout = max(1, int(getattr(settings, "INDIANAPI_TIMEOUT_SECONDS", 20)))

    @property
    def is_configured(self):
        return bool(self.api_key)

    def fetch_mutual_funds(self):
        if not self.is_configured:
            raise IndianAPIConfigurationError(
                "IndianAPI is not configured. Set INDIANAPI_API_KEY to enable optional enrichment."
            )
        if not self.base_url:
            raise IndianAPIConfigurationError("INDIANAPI_BASE_URL is empty.")
        try:
            response = requests.get(
                f"{self.base_url}/mutual_funds",
                headers={"X-Api-Key": self.api_key, "Accept": "application/json"},
                timeout=self.timeout,
            )
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
