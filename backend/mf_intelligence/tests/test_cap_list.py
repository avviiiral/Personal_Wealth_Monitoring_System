import io
from datetime import date
from unittest import mock

import requests
from django.test import SimpleTestCase, TestCase
from openpyxl import Workbook

from mf_intelligence.models import MFStockClassification
from mf_intelligence.services import cap_list


def amfi_workbook(rows, title="Average Market Capitalization of listed companies during the six months ended 30 June 2026"):
    workbook = Workbook()
    sheet = workbook.active
    sheet.append([title])
    sheet.append([
        "Sr. No.", "Company name", "ISIN", "BSE Symbol", "BSE 6 month Avg Total Market Cap in (Rs. Crs.)",
        "NSE Symbol", "NSE 6 month Avg Total Market Cap (Rs. Crs.)", "MSEI Symbol",
        "MSEI 6 month Avg Total Market Cap in (Rs Crs.)", "Average of All Exchanges (Rs. Cr.)",
        "Categorization as per SEBI Circular dated Oct 6, 2017",
    ])
    for row in rows:
        sheet.append(row)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


SAMPLE_ROWS = [
    (1, "Reliance Industries Ltd", "INE002A01018", "RELIANCE", 1873294.7, "RELIANCE", 1873278.8, "-", None, 1873286.77, "Large Cap"),
    (101, "Bosch Ltd", "INE323A01026", "BOSCHLTD", 106277.1, "BOSCHLTD", 106279.3, "-", None, 106278.22, "Mid Cap"),
    (251, "Navin Fluorine", "INE048G01026", "NAVINFLUOR", 33450.2, "NAVINFLUOR", 33433.6, "-", None, 33441.94, "Small Cap"),
    (None, "Footer note", "not-an-isin", None, None, None, None, None, None, None, None),
]


class ParsingTests(SimpleTestCase):
    def test_parses_period_and_rows_by_header_name(self):
        period, rows = cap_list.parse_amfi_workbook(amfi_workbook(SAMPLE_ROWS))
        self.assertEqual(period, date(2026, 6, 30))
        self.assertEqual([row["cap_bucket"] for row in rows], ["Large Cap", "Mid Cap", "Small Cap"])
        self.assertEqual(rows[1]["rank"], 101)
        self.assertEqual(str(rows[0]["avg_market_cap_crore"]), "1873286.7700")

    def test_rank_used_when_category_column_is_blank(self):
        rows = [(150, "Mid Co", "INE000M01010", None, None, None, None, None, None, 50000, None)]
        _, parsed = cap_list.parse_amfi_workbook(amfi_workbook(rows))
        self.assertEqual(parsed[0]["cap_bucket"], "Mid Cap")

    def test_workbook_without_header_yields_no_rows(self):
        workbook = Workbook()
        workbook.active.append(["nothing here"])
        buffer = io.BytesIO()
        workbook.save(buffer)
        self.assertEqual(cap_list.parse_amfi_workbook(buffer.getvalue()), (None, []))

    def test_candidate_periods_are_past_half_year_ends(self):
        self.assertEqual(cap_list.candidate_periods(date(2026, 11, 24)), [date(2026, 6, 30), date(2025, 12, 31), date(2025, 6, 30)])
        self.assertEqual(cap_list.candidate_periods(date(2027, 1, 10))[0], date(2026, 12, 31))

    def test_url_uses_amfi_file_naming(self):
        self.assertEqual(
            cap_list.amfi_url_for(date(2026, 6, 30)),
            "https://portal.amfiindia.com/spages/AverageMarketCapitalization30Jun2026.xlsx",
        )


def http_response(status, content=b"", content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"):
    response = mock.Mock()
    response.status_code = status
    response.content = content
    response.headers = {"Content-Type": content_type}
    return response


class RefreshTests(TestCase):
    def test_downloads_newest_available_period_and_replaces_old_rows(self):
        MFStockClassification.objects.create(isin="INE999Z01019", cap_bucket="Large Cap", source="AMFI",
                                             period_end=date(2025, 12, 31))
        MFStockClassification.objects.create(isin="INE111P01011", cap_bucket="Large Cap", source="INDEX_PROXY")
        urls = []

        def fake_get(url):
            urls.append(url)
            if "30Jun2026" in url:
                return http_response(200, amfi_workbook(SAMPLE_ROWS))
            return http_response(404, b"<html>", "text/html")

        result = cap_list.refresh_amfi_classification(today=date(2026, 11, 24), http_get=fake_get)
        self.assertEqual(result["status"], "imported")
        self.assertEqual(result["rows"], 3)
        amfi = MFStockClassification.objects.filter(source="AMFI")
        self.assertEqual(amfi.count(), 3)
        self.assertFalse(amfi.filter(isin="INE999Z01019").exists())  # dropped from the new list
        self.assertTrue(MFStockClassification.objects.filter(source="INDEX_PROXY").exists())
        self.assertEqual(cap_list.classification_meta()["method"], "AMFI")

    def test_connection_reset_is_retried_once(self):
        responses = iter([requests.ConnectionError("reset"), http_response(200, amfi_workbook(SAMPLE_ROWS))])
        result = cap_list.refresh_amfi_classification(today=date(2026, 11, 24), http_get=lambda url: _next(responses))
        self.assertEqual(result["status"], "imported")

    def test_current_period_is_not_downloaded_again(self):
        MFStockClassification.objects.create(isin="INE002A01018", cap_bucket="Large Cap", source="AMFI",
                                             period_end=date(2026, 6, 30))
        http_get = mock.Mock()
        result = cap_list.refresh_amfi_classification(today=date(2026, 11, 24), http_get=http_get)
        self.assertEqual(result["status"], "current")
        http_get.assert_not_called()

    def test_network_errors_and_html_pages_report_unavailable(self):
        responses = iter([
            requests.ConnectionError("down"), requests.ConnectionError("down again"),  # period 1, both attempts
            http_response(200, b"<html>", "text/html"),  # period 2 serves an error page
            http_response(404, b"", "text/html"),  # period 3 missing
        ])
        result = cap_list.refresh_amfi_classification(today=date(2026, 11, 24), http_get=lambda url: _next(responses))
        self.assertEqual(result, {"status": "unavailable"})
        self.assertFalse(MFStockClassification.objects.exists())


def _next(iterator):
    value = next(iterator)
    if isinstance(value, Exception):
        raise value
    return value
