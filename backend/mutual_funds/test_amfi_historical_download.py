from datetime import date
from unittest.mock import Mock, patch

from django.test import SimpleTestCase

from mutual_funds.services.amfi import AMFIService


class AMFIHistoricalWindowTests(SimpleTestCase):
    @patch("mutual_funds.services.amfi.AMFIService.parse_nav_file", return_value=[])
    @patch("mutual_funds.services.amfi.requests.get")
    @patch(
        "mutual_funds.services.amfi.AMFIService._resolve_nav_ids",
        return_value={"12345": {"mf_id": "17"}},
    )
    def test_historical_download_uses_seven_day_windows(
        self, resolve_nav_ids, get, parse_nav_file
    ):
        response = Mock()
        response.status_code = 200
        response.headers = {"Content-Type": "text/plain"}
        response.url = "https://portal.amfiindia.com/DownloadNAVHistoryReport_Po.aspx"
        response.content = (
            b"Scheme Code;Scheme Name;ISIN;ISIN Reinvestment;"
            b"Net Asset Value;Repurchase Price;Sale Price;Date"
        )
        response.text = response.content.decode("utf-8")
        response.raise_for_status.return_value = None
        get.return_value = response

        records = AMFIService._download_historical_api_records(
            date(2024, 1, 1),
            date(2024, 1, 15),
            ["12345"],
        )

        self.assertEqual(records, [])
        self.assertEqual(get.call_count, 3)
        self.assertEqual(
            [call.kwargs["params"] for call in get.call_args_list],
            [
                {
                    "mf": "17",
                    "tp": "1",
                    "frmdt": "01-Jan-2024",
                    "todt": "07-Jan-2024",
                },
                {
                    "mf": "17",
                    "tp": "1",
                    "frmdt": "08-Jan-2024",
                    "todt": "14-Jan-2024",
                },
                {
                    "mf": "17",
                    "tp": "1",
                    "frmdt": "15-Jan-2024",
                    "todt": "15-Jan-2024",
                },
            ],
        )
        resolve_nav_ids.assert_called_once_with({"12345"})
        self.assertEqual(parse_nav_file.call_count, 3)
