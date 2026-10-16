from unittest.mock import patch

from django.test import SimpleTestCase

from market_data.services.mis_reference_price_scheduler import (
    MISReferencePriceScheduler,
)


class MISReferencePriceSchedulerTests(SimpleTestCase):
    def setUp(self):
        # Keep scheduler lifecycle isolated; no real background threads or
        # external price-provider requests should start during these tests.
        self.started_before = MISReferencePriceScheduler._started
        MISReferencePriceScheduler._started = False

    def tearDown(self):
        MISReferencePriceScheduler._started = self.started_before

    @patch("market_data.services.mis_reference_price_scheduler.threading.Thread")
    def test_start_launches_scheduler_thread_once(self, thread_cls):
        thread = thread_cls.return_value

        MISReferencePriceScheduler.start()
        MISReferencePriceScheduler.start()

        thread_cls.assert_called_once()
        thread.start.assert_called_once()
        self.assertEqual(
            thread.call_args.kwargs["name"], "mis-reference-price-scheduler"
        )
        self.assertTrue(thread.call_args.kwargs["daemon"])

    @patch("market_data.services.mis_reference_price_scheduler.time.sleep")
    @patch("market_data.services.mis_reference_price_scheduler.close_old_connections")
    @patch("market_data.services.mis_reference_price_scheduler.MISReportService.refresh_reference_prices")
    def test_run_refreshes_prices_and_closes_connections(
        self, refresh, close_connections, sleep
    ):
        # Perform one refresh, then stop the scheduler's loop deterministically.
        sleep.side_effect = [None, KeyboardInterrupt]
        refresh.return_value = {
            "references": 3,
            "refreshed": 3,
            "failed": 0,
            "records": 90,
        }

        with self.assertRaises(KeyboardInterrupt):
            MISReferencePriceScheduler._run()

        refresh.assert_called_once_with()
        self.assertGreaterEqual(close_connections.call_count, 2)
