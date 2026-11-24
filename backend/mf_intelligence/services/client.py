"""HTTP client for the public CharlieAlpha mutual fund API.

The API is unauthenticated, GET-only and limited to 50 requests per 10
seconds. The client keeps a shared sliding-window limiter below that
ceiling (configurable) so concurrent fetch workers never trip it, and it
retries 429 / 5xx / network failures with back-off.
"""

import logging
import threading
import time
from collections import deque

import requests

from config.pwms_config import get as get_pwms_config


logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://api.charliealpha.in/v1"


def _config(key, default):
    return get_pwms_config("mf_intelligence", key, default)


class CharlieAlphaError(Exception):
    """The API could not return a usable response."""


class CharlieAlphaNotFound(CharlieAlphaError):
    """The API answered 404 for the requested resource."""


class SlidingWindowRateLimiter:
    """Allow at most ``max_requests`` calls in any ``window_seconds`` span."""

    def __init__(self, max_requests, window_seconds, clock=time.monotonic, sleep=time.sleep):
        self.max_requests = max(1, int(max_requests))
        self.window_seconds = float(window_seconds)
        self._clock = clock
        self._sleep = sleep
        self._calls = deque()
        self._lock = threading.Lock()

    def acquire(self):
        while True:
            with self._lock:
                now = self._clock()
                while self._calls and now - self._calls[0] >= self.window_seconds:
                    self._calls.popleft()
                if len(self._calls) < self.max_requests:
                    self._calls.append(now)
                    return
                wait = self.window_seconds - (now - self._calls[0])
            self._sleep(max(wait, 0.01))


class CharlieAlphaClient:
    def __init__(
        self,
        base_url=None,
        timeout=None,
        max_requests=None,
        window_seconds=None,
        max_retries=None,
        session=None,
        sleep=time.sleep,
    ):
        self.base_url = (base_url or _config("base_url", DEFAULT_BASE_URL)).rstrip("/")
        self.timeout = timeout or _config("timeout_seconds", 45)
        self.max_retries = _config("max_retries", 3) if max_retries is None else max_retries
        self._sleep = sleep
        self.limiter = SlidingWindowRateLimiter(
            max_requests or _config("max_requests_per_window", 40),
            window_seconds or _config("rate_window_seconds", 10),
            sleep=sleep,
        )
        self._local = threading.local()
        self._shared_session = session
        self.request_count = 0
        self._count_lock = threading.Lock()

    # requests.Session is not guaranteed thread-safe, so every worker
    # thread gets its own session unless a test injected one.
    def _session(self):
        if self._shared_session is not None:
            return self._shared_session
        session = getattr(self._local, "session", None)
        if session is None:
            session = requests.Session()
            session.headers.update(
                {
                    "Accept": "application/json",
                    "User-Agent": _config("user_agent", "PWMS-MFIntelligence/1.0"),
                }
            )
            self._local.session = session
        return session

    def get(self, path, params=None):
        url = f"{self.base_url}/{path.lstrip('/')}"
        attempt = 0
        while True:
            attempt += 1
            self.limiter.acquire()
            with self._count_lock:
                self.request_count += 1
            try:
                response = self._session().get(url, params=params, timeout=self.timeout)
            except requests.RequestException as exc:
                if attempt > self.max_retries:
                    raise CharlieAlphaError(f"GET {path} failed: {exc}") from exc
                self._backoff(attempt, None)
                continue

            status = response.status_code
            if status == 404:
                raise CharlieAlphaNotFound(f"GET {path} returned 404")
            if status == 429 or status >= 500:
                if attempt > self.max_retries:
                    raise CharlieAlphaError(f"GET {path} returned HTTP {status}")
                self._backoff(attempt, response.headers.get("Retry-After"))
                continue
            if status >= 400:
                raise CharlieAlphaError(f"GET {path} returned HTTP {status}")
            try:
                return response.json()
            except ValueError as exc:
                raise CharlieAlphaError(f"GET {path} returned invalid JSON") from exc

    def _backoff(self, attempt, retry_after):
        delay = None
        if retry_after:
            try:
                delay = float(retry_after)
            except (TypeError, ValueError):
                delay = None
        if delay is None:
            delay = min(30.0, 2.0 ** attempt)
        logger.info("CharlieAlpha back-off %.1fs (attempt %s)", delay, attempt)
        self._sleep(delay)

    # Endpoint helpers -------------------------------------------------

    def categories(self):
        return self.get("mf/categories")

    def schemes(self, category_slug=None):
        params = {"cat": category_slug} if category_slug else None
        return self.get("mf/schemes", params=params)

    def metrics_by_category(self, category_slug):
        return self.get(f"mf/metrics-by-cat/{category_slug}")

    def nav_history(self, amfi_code):
        return self.get(f"mf/hnav/{amfi_code}")

    def holdings(self, amfi_code, as_of=None, change=True):
        params = {"change": "yes" if change else "no"}
        if as_of:
            params["as_of"] = str(as_of)
        return self.get(f"mf/holdings/{amfi_code}", params=params)

    def holdings_periods(self, amfi_code):
        return self.get(f"mf/holdings-periods/{amfi_code}")

    def aum_history(self, amfi_code):
        return self.get(f"mf/aum/{amfi_code}")

    def benchmarks(self):
        return self.get("benchmark/list")

    def benchmark_history(self, slug):
        return self.get(f"benchmark/hnav/{slug}")
