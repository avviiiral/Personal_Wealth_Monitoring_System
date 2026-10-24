from datetime import date
from unittest.mock import patch

import pandas as pd
from django.test import TestCase

from investments.models import Asset
from market_data.services.yahoo_finance import YahooFinanceService


class YahooFinanceClosePriceTests(TestCase):
    @patch("market_data.services.yahoo_finance.MarketPrice.objects.bulk_create")
    @patch("market_data.services.yahoo_finance.YahooFinanceService.fetch_history")
    def test_skips_rows_without_close_and_saves_valid_rows(
        self, fetch_history, bulk_create
    ):
        fetch_history.return_value = pd.DataFrame(
            {
                "Open": [100.0, 101.0],
                "High": [110.0, 111.0],
                "Low": [95.0, 96.0],
                "Close": [None, 105.0],
                "Adj Close": [None, 104.0],
                "Volume": [1000, 2000],
            },
            index=pd.to_datetime(["2026-10-22", "2026-10-23"]),
        )
        asset = Asset(id=1, name="Test Asset")

        count = YahooFinanceService.save_history(asset, "TEST.NS")

        self.assertEqual(count, 1)
        bulk_create.assert_called_once()
        records = bulk_create.call_args.args[0]
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].date, date(2026, 10, 23))
        self.assertEqual(str(records[0].close_price), "105.0")

    @patch("market_data.services.yahoo_finance.MarketPrice.objects.bulk_create")
    @patch("market_data.services.yahoo_finance.YahooFinanceService.fetch_history")
    def test_raises_when_no_rows_have_a_valid_close(
        self, fetch_history, bulk_create
    ):
        fetch_history.return_value = pd.DataFrame(
            {
                "Open": [100.0],
                "High": [110.0],
                "Low": [95.0],
                "Close": [None],
                "Adj Close": [None],
                "Volume": [1000],
            },
            index=pd.to_datetime(["2026-10-23"]),
        )
        asset = Asset(id=1, name="Test Asset")

        with self.assertRaisesRegex(ValueError, "valid Close"):
            YahooFinanceService.save_history(asset, "TEST.NS")

        bulk_create.assert_not_called()
