"""Polite HTTP access for the masterdata pipeline.

* identifying User-Agent (override with PWMS_MF_USER_AGENT)
* timeouts on every request
* 3 retries with exponential backoff for network errors, HTTP 429 and 5xx
  (Retry-After is honoured)
* per-host minimum spacing between requests (rate limiting); mfapi.in uses
  PWMS_MFAPI_MIN_INTERVAL_SECONDS (default 1.0s)
"""
from __future__ import annotations

import logging
import os
import threading
import time
from urllib.parse import urlparse

import requests

logger = logging.getLogger(__name__)

USER_AGENT = os.environ.get(
    "PWMS_MF_USER_AGENT",
    "PWMS-MF-Masterdata/1.0 (+https://github.com/avviiiral/Personal_Wealth_Monitoring_System)",
)
DEFAULT_TIMEOUT = 60
DEFAULT_RETRIES = 3
DEFAULT_BACKOFF = 2.0
RETRY_STATUS = {429, 500, 502, 503, 504}

_HOST_LOCK = threading.Lock()
_HOST_LAST = {}


def host_min_interval(host: str) -> float:
    if host.endswith("mfapi.in"):
        return float(os.environ.get("PWMS_MFAPI_MIN_INTERVAL_SECONDS", "1.0"))
    return float(os.environ.get("PWMS_MF_HOST_MIN_INTERVAL_SECONDS", "1.0"))


def throttle(host: str) -> None:
    """Block until at least host_min_interval(host) has passed since the last request."""
    interval = host_min_interval(host)
    with _HOST_LOCK:
        wait = interval - (time.monotonic() - _HOST_LAST.get(host, 0.0))
        if wait > 0:
            time.sleep(wait)
        _HOST_LAST[host] = time.monotonic()


def _retry_after(response) -> float | None:
    try:
        return float(response.headers.get("Retry-After", ""))
    except (TypeError, ValueError):
        return None


class PoliteHTTPClient:
    def __init__(self, *, timeout=DEFAULT_TIMEOUT, retries=DEFAULT_RETRIES, backoff=DEFAULT_BACKOFF, sleep=time.sleep):
        self.timeout = timeout
        self.retries = retries
        self.backoff = backoff
        self.sleep = sleep

    def get(self, url, *, params=None, headers=None) -> requests.Response:
        host = urlparse(url).netloc.casefold()
        merged = {"User-Agent": USER_AGENT, "Accept": "*/*", **(headers or {})}
        last_error = None
        for attempt in range(self.retries + 1):
            throttle(host)
            try:
                response = requests.get(url, params=params, headers=merged, timeout=self.timeout)
                if response.status_code in RETRY_STATUS:
                    raise requests.HTTPError(f"HTTP {response.status_code} from {host}", response=response)
                response.raise_for_status()
                return response
            except requests.RequestException as exc:
                last_error = exc
                status = getattr(getattr(exc, "response", None), "status_code", None)
                if status is not None and status not in RETRY_STATUS:
                    break  # 4xx other than 429: retrying will not help
                if attempt < self.retries:
                    delay = _retry_after(exc.response) if getattr(exc, "response", None) is not None else None
                    delay = delay if delay is not None else self.backoff * (2 ** attempt)
                    logger.warning("GET %s failed (%s); retry %s/%s in %.1fs", url, exc, attempt + 1, self.retries, delay)
                    self.sleep(delay)
        raise RuntimeError(f"GET {url} failed after {self.retries} retries: {last_error}") from last_error


def with_retries(func, *, retries=DEFAULT_RETRIES, backoff=DEFAULT_BACKOFF, sleep=time.sleep, label="request"):
    """Retry a callable (used around amfipy/httpx calls) with exponential backoff."""
    last_error = None
    for attempt in range(retries + 1):
        try:
            return func()
        except Exception as exc:  # httpx errors are not imported here on purpose
            last_error = exc
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status is not None and status not in RETRY_STATUS:
                break
            if attempt < retries:
                delay = backoff * (2 ** attempt)
                logger.warning("%s failed (%s); retry %s/%s in %.1fs", label, exc, attempt + 1, retries, delay)
                sleep(delay)
    raise RuntimeError(f"{label} failed after {retries} retries: {last_error}") from last_error


def amfipy_client():
    """amfipy AMFIClient with a polite User-Agent and per-host throttling.

    amfipy fixes its own timeout (60s) and headers; an httpx request hook is
    the supported way to adjust both User-Agent and pacing without patching it.
    """
    try:
        from amfipy import AMFIClient
    except ImportError as exc:  # pragma: no cover - depends on environment
        raise RuntimeError("amfipy is not installed. Run: pip install amfipy==0.3.0") from exc

    def on_request(request):
        throttle(request.url.host.casefold())
        request.headers["User-Agent"] = USER_AGENT

    return AMFIClient(event_hooks={"request": [on_request]})
