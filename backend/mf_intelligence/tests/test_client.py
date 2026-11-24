from unittest import mock

import requests
from django.test import SimpleTestCase

from mf_intelligence.services.client import (
    CharlieAlphaClient,
    CharlieAlphaError,
    CharlieAlphaNotFound,
    SlidingWindowRateLimiter,
)


class FakeClock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


class RateLimiterTests(SimpleTestCase):
    def test_blocks_once_window_is_full(self):
        clock = FakeClock()
        limiter = SlidingWindowRateLimiter(3, 10, clock=clock.time, sleep=clock.sleep)
        for _ in range(3):
            limiter.acquire()
        self.assertEqual(clock.sleeps, [])
        limiter.acquire()
        self.assertEqual(len(clock.sleeps), 1)
        self.assertAlmostEqual(clock.sleeps[0], 10.0)

    def test_window_slides(self):
        clock = FakeClock()
        limiter = SlidingWindowRateLimiter(2, 10, clock=clock.time, sleep=clock.sleep)
        limiter.acquire()
        clock.now = 6
        limiter.acquire()
        clock.now = 10.5  # first call has left the window
        limiter.acquire()
        self.assertEqual(clock.sleeps, [])


def response(status, payload=None, headers=None, invalid_json=False):
    result = mock.Mock()
    result.status_code = status
    result.headers = headers or {}
    if invalid_json:
        result.json.side_effect = ValueError("not json")
    else:
        result.json.return_value = payload
    return result


class ClientTests(SimpleTestCase):
    def make_client(self, responses):
        session = mock.Mock()
        session.get.side_effect = responses
        sleeps = []
        client = CharlieAlphaClient(
            base_url="https://example.test/v1",
            max_requests=100,
            window_seconds=1,
            max_retries=2,
            session=session,
            sleep=sleeps.append,
        )
        return client, session, sleeps

    def test_successful_get_builds_url_and_params(self):
        client, session, _ = self.make_client([response(200, {"ok": True})])
        self.assertEqual(client.holdings("148918", as_of="2026-09-30"), {"ok": True})
        session.get.assert_called_once_with(
            "https://example.test/v1/mf/holdings/148918",
            params={"change": "yes", "as_of": "2026-09-30"},
            timeout=client.timeout,
        )
        self.assertEqual(client.request_count, 1)

    def test_retries_rate_limit_using_retry_after(self):
        client, session, sleeps = self.make_client([
            response(429, headers={"Retry-After": "3"}),
            response(200, {"dates": []}),
        ])
        self.assertEqual(client.nav_history("1"), {"dates": []})
        self.assertEqual(sleeps, [3.0])
        self.assertEqual(session.get.call_count, 2)

    def test_retries_server_and_network_errors_then_gives_up(self):
        client, _, sleeps = self.make_client([
            response(503),
            requests.ConnectionError("reset"),
            response(502),
        ])
        with self.assertRaises(CharlieAlphaError):
            client.categories()
        self.assertEqual(len(sleeps), 2)

    def test_not_found_is_not_retried(self):
        client, session, _ = self.make_client([response(404)])
        with self.assertRaises(CharlieAlphaNotFound):
            client.aum_history("1")
        self.assertEqual(session.get.call_count, 1)

    def test_invalid_json_raises(self):
        client, _, _ = self.make_client([response(200, invalid_json=True)])
        with self.assertRaises(CharlieAlphaError):
            client.benchmarks()
