"""Client for the Indian Stock Market API (indianapi.in), mutual fund endpoints only.

Only the two documented mutual fund endpoints are called:

* ``GET /mutual_fund_search?query=...`` - scheme identity lookup.
* ``GET /mutual_funds`` - latest NAV, AUM, trailing returns and star rating.

The key comes from ``INDIAN_API_KEY`` and is sent in the ``X-Api-Key`` header.
The key is optional: without it the Fund Intelligence page serves stored data
only and reports live enrichment as not configured.
"""

from __future__ import annotations

import requests
from django.conf import settings


class IndianApiError(Exception):
    """The provider could not be reached or returned an unusable response."""


class IndianApiNotConfigured(IndianApiError):
    """``INDIAN_API_KEY`` is not set, so no request was made."""


class IndianApiClient:
    timeout_seconds = 15
    api_key_header = "X-Api-Key"

    def __init__(self, api_key=None, base_url=None, session=None):
        if api_key is None:
            api_key = getattr(settings, "INDIAN_API_KEY", "")
        if base_url is None:
            base_url = getattr(settings, "INDIAN_API_BASE_URL", "https://stock.indianapi.in")
        self.api_key = (api_key or "").strip()
        self.base_url = (base_url or "").strip().rstrip("/")
        self.session = session or requests.Session()

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key)

    def _get(self, path, params=None):
        if not self.is_configured:
            raise IndianApiNotConfigured("INDIAN_API_KEY is not set.")
        try:
            response = self.session.get(
                f"{self.base_url}{path}",
                params=params,
                headers={self.api_key_header: self.api_key, "Accept": "application/json"},
                timeout=self.timeout_seconds,
            )
        except requests.RequestException as exc:
            # Never echo the exception text: it can include request details.
            raise IndianApiError(f"Indian API request failed ({exc.__class__.__name__}).") from exc
        if response.status_code != 200:
            raise IndianApiError(f"Indian API returned HTTP {response.status_code} for {path}.")
        try:
            return response.json()
        except ValueError as exc:
            raise IndianApiError(f"Indian API returned a non-JSON body for {path}.") from exc

    def mutual_fund_search(self, query: str) -> list:
        data = self._get("/mutual_fund_search", params={"query": query})
        return data if isinstance(data, list) else []

    def mutual_funds(self):
        return self._get("/mutual_funds")
