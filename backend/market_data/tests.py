from io import StringIO
from unittest.mock import patch

from django.test import SimpleTestCase

from market_data.management.commands.refresh_mis_reference_prices import Command


class MISReferenceStepTests(SimpleTestCase):
    """The MIS reference refresh moved into the 15-minute market_prices job
    (it replaces the removed MISReferencePriceScheduler)."""

    def _job(self):
        from market_data.jobs import MarketPricesJob

        return MarketPricesJob(deadline_seconds=5, mis_reference=True)

    def test_refreshes_when_reference_prices_are_stale(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo

        job = self._job()
        refresh_result = {"references": 3, "refreshed": 3, "failed": 0, "records": 90}
        with patch.object(job, "_mis_references_stale", return_value=True), \
                patch("portfolio.mis_report_service.MISReportService.refresh_reference_prices",
                      return_value=refresh_result) as refresh:
            counts = job._run_mis_references(datetime(2026, 11, 25, 11, 0, tzinfo=ZoneInfo("Asia/Kolkata")))
        refresh.assert_called_once_with()
        self.assertEqual(counts["mis_refreshed"], 3)

    def test_skips_when_reference_prices_are_current(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo

        job = self._job()
        with patch.object(job, "_mis_references_stale", return_value=False), \
                patch("portfolio.mis_report_service.MISReportService.refresh_reference_prices") as refresh:
            counts = job._run_mis_references(datetime(2026, 11, 25, 20, 0, tzinfo=ZoneInfo("Asia/Kolkata")))
        refresh.assert_not_called()
        self.assertEqual(counts, {"mis_current": 1})


class RefreshMISReferencePricesCommandTests(SimpleTestCase):
    @patch(
        "market_data.management.commands.refresh_mis_reference_prices."
        "MISReportService.refresh_reference_prices"
    )
    def test_command_reports_refresh_summary(self, refresh):
        refresh.return_value = {
            "references": 4,
            "refreshed": 3,
            "failed": 1,
            "records": 120,
        }
        command = Command()
        command.stdout = StringIO()

        command.handle()

        output = command.stdout.getvalue()
        self.assertIn("3/4 references refreshed", output)
        self.assertIn("1 failed", output)
        self.assertIn("120 records saved", output)
