"""Unit tests for cross-process AMFI refresh coordination."""
from contextlib import nullcontext
from unittest.mock import patch

from django.test import SimpleTestCase

from market_data.services.investment_amfi_scheduler import InvestmentAMFIScheduler


class InvestmentAMFISchedulerLockTests(SimpleTestCase):
    def test_refresh_is_skipped_when_another_process_holds_lock(self):
        with (
            patch(
                "config.postgres_advisory_lock.postgres_advisory_lock",
                return_value=nullcontext(False),
            ),
            patch.object(InvestmentAMFIScheduler, "_run_refresh_locked") as refresh,
            patch(
                "market_data.services.investment_amfi_scheduler.close_old_connections"
            ),
        ):
            InvestmentAMFIScheduler._run_refresh()

        refresh.assert_not_called()

    def test_refresh_runs_when_lock_is_acquired(self):
        with (
            patch(
                "config.postgres_advisory_lock.postgres_advisory_lock",
                return_value=nullcontext(True),
            ),
            patch.object(InvestmentAMFIScheduler, "_run_refresh_locked") as refresh,
            patch(
                "market_data.services.investment_amfi_scheduler.close_old_connections"
            ),
        ):
            InvestmentAMFIScheduler._run_refresh()

        refresh.assert_called_once_with()
