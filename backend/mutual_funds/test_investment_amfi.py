from unittest.mock import patch

from django.test import SimpleTestCase

from mutual_funds.services.investment_amfi import InvestmentAMFIService


class InvestmentAMFIServiceTests(SimpleTestCase):
    @patch("mutual_funds.services.investment_amfi.AMFIService._import_master_records")
    @patch("mutual_funds.services.investment_amfi.AMFIService.parse_nav_file")
    @patch("mutual_funds.services.investment_amfi.AMFIService.download_latest_nav")
    def test_refresh_matches_growth_isin_only(
        self,
        download_latest_nav,
        parse_nav_file,
        import_master_records,
    ):
        download_latest_nav.return_value = "amfi"
        parse_nav_file.return_value = [
            {
                "scheme_code": "123",
                "scheme_name": "Held Fund Direct Growth",
                "isin_growth": "INFHELD000001",
                "isin_dividend": None,
                "nav": "100.0",
                "date": None,
            },
            {
                "scheme_code": "999",
                "scheme_name": "Unheld Fund",
                "isin_growth": "INFUNHELD00001",
                "isin_dividend": None,
                "nav": "50.0",
                "date": None,
            },
        ]
        import_master_records.return_value = {
            "schemes": 1,
            "nav_records": 1,
        }

        result = InvestmentAMFIService.refresh_for_isins(
            ["infheld000001", "INFUNHELD00001"]
        )

        self.assertEqual(result["requested_isins"], 2)
        self.assertEqual(result["matched_isins"], 1)
        self.assertEqual(result["unmatched_isins"], ["INFUNHELD00001"])
        self.assertEqual(result["schemes"], 1)
        self.assertEqual(result["nav_records"], 1)
        import_master_records.assert_called_once()
        matched_records = import_master_records.call_args.args[0]
        self.assertEqual([row["scheme_code"] for row in matched_records], ["123"])

    @patch("mutual_funds.services.investment_amfi.AMFIService._import_master_records")
    @patch("mutual_funds.services.investment_amfi.AMFIService.parse_nav_file")
    @patch("mutual_funds.services.investment_amfi.AMFIService.download_latest_nav")
    def test_refresh_matches_dividend_isin(
        self,
        download_latest_nav,
        parse_nav_file,
        import_master_records,
    ):
        download_latest_nav.return_value = "amfi"
        parse_nav_file.return_value = [
            {
                "scheme_code": "456",
                "scheme_name": "Held Fund IDCW",
                "isin_growth": None,
                "isin_dividend": "INFIDCW000001",
                "nav": "25.0",
                "date": None,
            },
        ]
        import_master_records.return_value = {
            "schemes": 1,
            "nav_records": 1,
        }

        result = InvestmentAMFIService.refresh_for_isins(["INFIDCW000001"])

        self.assertEqual(result["matched_isins"], 1)
        self.assertEqual(result["unmatched_isins"], [])
        matched_records = import_master_records.call_args.args[0]
        self.assertEqual(matched_records[0]["scheme_code"], "456")
