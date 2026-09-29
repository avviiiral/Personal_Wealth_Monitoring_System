from datetime import date
from decimal import Decimal

from dateutil.relativedelta import relativedelta

from django.contrib.auth.models import User
from django.test import TestCase

from users.models import FamilyGroup

from mutual_funds.models import (
    AMFIMasterScheme,
    MutualFundHolding,
    MutualFundNAV,
    MutualFundScheme,
    MutualFundTransaction,
    MutualFundTransactionType,
    SIP,
    SIPFrequency,
    SIPInstallment,
    SIPInstallmentStatus,
)

from mutual_funds.services.sip_engine import SIPEngine


class SIPEngineTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="testuser",
            password="testpassword",
        )

        self.scheme = MutualFundScheme.objects.create(
            owner=self.user,
            scheme_name="Test Mutual Fund",
            amc_name="Test AMC",
            scheme_code="TEST001",
            plan="Direct",
            option="Growth",
            category="Equity",
            is_active=True,
        )

        # Anchored to the first of the CURRENT month, computed at
        # test-run time, rather than a fixed calendar date. These
        # tests used to hardcode dates around "today is
        # 2026-08-12", which silently broke the moment the real
        # clock moved past the dates baked into the fixtures.
        # Anchoring on day=1 specifically sidesteps relativedelta's
        # month-length clamping (e.g. Mar 31 minus a month lands on
        # Feb 28, not "Mar 3") ever affecting these comparisons.
        self.anchor = date.today().replace(day=1)

    def create_sip(
        self,
        start_date=None,
        next_installment_date=None,
        frequency=SIPFrequency.MONTHLY,
        amount=Decimal("5000.00"),
        end_date=None,
        is_active=True,
    ):
        if start_date is None:
            start_date = self.anchor - relativedelta(months=6)

        if next_installment_date is None:
            next_installment_date = self.anchor - relativedelta(months=1)

        return SIP.objects.create(
            owner=self.user,
            scheme=self.scheme,
            amount=amount,
            frequency=frequency,
            start_date=start_date,
            end_date=end_date,
            next_installment_date=next_installment_date,
            is_active=is_active,
        )

    # --------------------------------------------------
    # calculate_next_date
    #
    # A pure function of its two arguments - no dependency on
    # "today", so fixed calendar dates here are fine and
    # intentional (they're just exercising month/quarter/year
    # rollover arithmetic).
    # --------------------------------------------------

    def test_calculate_next_date_weekly(self):
        result = SIPEngine.calculate_next_date(
            date(2026, 8, 1),
            SIPFrequency.WEEKLY,
        )

        self.assertEqual(
            result,
            date(2026, 8, 8),
        )

    def test_calculate_next_date_monthly(self):
        result = SIPEngine.calculate_next_date(
            date(2026, 1, 1),
            SIPFrequency.MONTHLY,
        )

        self.assertEqual(
            result,
            date(2026, 2, 1),
        )

    def test_calculate_next_date_quarterly(self):
        result = SIPEngine.calculate_next_date(
            date(2026, 1, 1),
            SIPFrequency.QUARTERLY,
        )

        self.assertEqual(
            result,
            date(2026, 4, 1),
        )

    def test_calculate_next_date_yearly(self):
        result = SIPEngine.calculate_next_date(
            date(2026, 1, 1),
            SIPFrequency.YEARLY,
        )

        self.assertEqual(
            result,
            date(2027, 1, 1),
        )

    def test_calculate_next_date_invalid_frequency(self):
        with self.assertRaises(ValueError):
            SIPEngine.calculate_next_date(
                date(2026, 1, 1),
                "INVALID",
            )

    # --------------------------------------------------
    # get_due_count
    # --------------------------------------------------

    def test_due_count_counts_overdue_installments(self):
        """
        Relative to "the first of this month" (self.anchor):

            anchor - 1 month -> due
            anchor            -> due
            anchor + 1 month  -> future

        Therefore the due count must be 2, regardless of which
        real-world month or day-of-month the test happens to run
        on.
        """

        sip = self.create_sip(
            start_date=self.anchor - relativedelta(months=6),
            next_installment_date=self.anchor - relativedelta(months=1),
            frequency=SIPFrequency.MONTHLY,
        )

        due_count = SIPEngine.get_due_count(sip)

        self.assertEqual(
            due_count,
            2,
        )

    def test_due_count_zero_for_inactive_sip(self):
        sip = self.create_sip(
            is_active=False,
        )

        self.assertEqual(
            SIPEngine.get_due_count(sip),
            0,
        )

    def test_due_count_zero_before_start_date(self):
        future_date = self.anchor + relativedelta(months=1)

        sip = self.create_sip(
            start_date=future_date,
            next_installment_date=future_date,
        )

        self.assertEqual(
            SIPEngine.get_due_count(sip),
            0,
        )

    def test_due_count_respects_end_date(self):
        sip = self.create_sip(
            start_date=self.anchor - relativedelta(months=6),
            next_installment_date=self.anchor - relativedelta(months=1),
            frequency=SIPFrequency.MONTHLY,
            end_date=self.anchor - relativedelta(days=1),
        )

        self.assertEqual(
            SIPEngine.get_due_count(sip),
            1,
        )

    # --------------------------------------------------
    # is_due
    # --------------------------------------------------

    def test_is_due_returns_true_when_installment_is_due(self):
        sip = self.create_sip(
            next_installment_date=self.anchor - relativedelta(months=1),
        )

        self.assertTrue(
            SIPEngine.is_due(sip)
        )

    def test_is_due_returns_false_when_installment_is_not_due(self):
        sip = self.create_sip(
            next_installment_date=self.anchor + relativedelta(months=1),
        )

        self.assertFalse(
            SIPEngine.is_due(sip)
        )

    # --------------------------------------------------
    # get_due_sips
    # --------------------------------------------------

    def test_get_due_sips_returns_only_due_active_sips(self):
        due_sip = self.create_sip(
            next_installment_date=self.anchor - relativedelta(months=1),
        )

        future_sip = SIP.objects.create(
            owner=self.user,
            scheme=self.scheme,
            amount=Decimal("3000.00"),
            frequency=SIPFrequency.MONTHLY,
            start_date=self.anchor - relativedelta(months=6),
            next_installment_date=self.anchor + relativedelta(months=1),
            is_active=True,
        )

        inactive_sip = SIP.objects.create(
            owner=self.user,
            scheme=self.scheme,
            amount=Decimal("2000.00"),
            frequency=SIPFrequency.MONTHLY,
            start_date=self.anchor - relativedelta(months=6),
            next_installment_date=self.anchor - relativedelta(months=1),
            is_active=False,
        )

        result = SIPEngine.get_due_sips(
            self.user
        )

        self.assertIn(
            due_sip,
            result,
        )

        self.assertNotIn(
            future_sip,
            result,
        )

        self.assertNotIn(
            inactive_sip,
            result,
        )

    # --------------------------------------------------
    # get_sip_status
    # --------------------------------------------------

    def test_sip_status_due(self):
        sip = self.create_sip(
            next_installment_date=self.anchor - relativedelta(months=1),
        )

        result = SIPEngine.get_sip_status(
            sip
        )

        self.assertEqual(
            result["status"],
            "DUE",
        )

        self.assertEqual(
            result["due_count"],
            2,
        )

    def test_sip_status_upcoming(self):
        future_date = self.anchor + relativedelta(months=1)

        sip = self.create_sip(
            start_date=future_date,
            next_installment_date=future_date,
        )

        result = SIPEngine.get_sip_status(
            sip
        )

        self.assertEqual(
            result["status"],
            "UPCOMING",
        )

        self.assertEqual(
            result["due_count"],
            0,
        )

    def test_sip_status_inactive(self):
        sip = self.create_sip(
            is_active=False,
        )

        result = SIPEngine.get_sip_status(
            sip
        )

        self.assertEqual(
            result["status"],
            "INACTIVE",
        )

        self.assertEqual(
            result["due_count"],
            0,
        )

    def test_sip_status_completed(self):
        sip = self.create_sip(
            start_date=self.anchor - relativedelta(months=6),
            next_installment_date=self.anchor - relativedelta(months=1),
            end_date=self.anchor - relativedelta(days=1),
        )

        result = SIPEngine.get_sip_status(
            sip
        )

        self.assertEqual(
            result["status"],
            "COMPLETED",
        )

    # --------------------------------------------------
    # execute_sip validation
    # --------------------------------------------------

    def test_execute_sip_rejects_inactive_sip(self):
        sip = self.create_sip(
            is_active=False,
        )

        with self.assertRaisesMessage(
            ValueError,
            "Cannot execute an inactive SIP.",
        ):
            SIPEngine.execute_sip(sip)

    def test_execute_sip_rejects_non_due_sip(self):
        sip = self.create_sip(
            next_installment_date=self.anchor + relativedelta(months=1),
        )

        with self.assertRaisesMessage(
            ValueError,
            "SIP installment is not due.",
        ):
            SIPEngine.execute_sip(sip)

# ==================================================================
# AMFI NAV IMPORT - BATCHED COMMITS
# ==================================================================
#
# Regression coverage for importing NAV records in bounded-size
# batches (see services.amfi.AMFIService.NAV_IMPORT_BATCH_SIZE)
# instead of one single transaction spanning the whole AMFI file
# (which, for a full ~14,000-scheme file, could hold SQLite's
# write lock long enough to surface as "database is locked" errors
# on unrelated concurrent requests).

from unittest.mock import patch

from mutual_funds.services.amfi import AMFIService


class AMFINavImportBatchingTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="amfi_batch_test",
            password="test-password",
        )
        family = FamilyGroup.objects.create(name="AMFI Batch Family")
        self.user.profile.family_groups.add(family)

    def _make_records(self, count):
        return [
            {
                "scheme_code": f"SC{i:04d}",
                "scheme_name": f"Test Scheme {i}",
                "isin_growth": "",
                "isin_dividend": "",
                "date": date(2026, 1, 1),
                "nav": Decimal("10.00") + i,
            }
            for i in range(count)
        ]

    def test_import_records_across_multiple_batches(self):
        # Small batch size so a modest record count still exercises
        # more than one batch/transaction.
        with patch.object(AMFIService, "NAV_IMPORT_BATCH_SIZE", 3):
            result = AMFIService._import_records(
                self.user,
                self._make_records(7),
            )

        self.assertEqual(result["schemes"], 7)
        self.assertEqual(result["nav_records"], 7)

        self.assertEqual(
            MutualFundScheme.objects.filter(
                owner=self.user
            ).count(),
            7,
        )

    def test_import_records_is_idempotent(self):
        records = self._make_records(5)

        with patch.object(AMFIService, "NAV_IMPORT_BATCH_SIZE", 2):
            AMFIService._import_records(self.user, records)
            result = AMFIService._import_records(self.user, records)

        # Re-importing the same records updates rather than
        # duplicates them.
        self.assertEqual(result["schemes"], 5)
        self.assertEqual(
            MutualFundScheme.objects.filter(
                owner=self.user
            ).count(),
            5,
        )
        self.assertEqual(
            MutualFundNAV.objects.filter(
                scheme__owner=self.user
            ).count(),
            5,
        )

    def test_import_records_empty_list(self):
        result = AMFIService._import_records(self.user, [])

        self.assertEqual(result["schemes"], 0)
        self.assertEqual(result["nav_records"], 0)



class AMFIHistoricalMasterImportTests(TestCase):
    """Regression coverage for the live AMFI historical report contract."""

    def test_historical_report_parser_uses_report_header_positions(self):
        text = (
            "Scheme Code;Scheme Name;ISIN Div Payout/ISIN Growth;"
            "ISIN Div Reinvestment;Net Asset Value;Repurchase Price;"
            "Sale Price;Date\n"
            "119551;SBI Bluechip Fund - Direct Plan - Growth;"
            "INF200K01A11;-;123.4567;123.4567;123.4567;29-Sep-2026\n"
            "120503;ICICI Prudential Bluechip Fund - Direct Plan - Growth;"
            "INF109K01X12;-;234.5678;234.5678;234.5678;29-Sep-2026\n"
        )

        records = AMFIService.parse_nav_file(
            text,
            historical=True,
            scheme_codes={"119551", "120503"},
        )

        self.assertEqual(len(records), 2)
        self.assertEqual(records[0]["scheme_code"], "119551")
        self.assertEqual(records[0]["nav"], Decimal("123.4567"))
        self.assertEqual(records[0]["date"], date(2026, 9, 29))
        self.assertEqual(records[1]["scheme_code"], "120503")
        self.assertEqual(records[1]["nav"], Decimal("234.5678"))

    def test_historical_report_filter_does_not_match_unrequested_scheme(self):
        text = (
            "Scheme Code;Scheme Name;ISIN Div Payout/ISIN Growth;"
            "ISIN Div Reinvestment;Net Asset Value;Repurchase Price;"
            "Sale Price;Date\n"
            "119551;Requested Fund;INF000000001;-;10.00;10.00;10.00;29-Sep-2026\n"
            "999999;Other Fund;INF000000999;-;20.00;20.00;20.00;29-Sep-2026\n"
        )

        records = AMFIService.parse_nav_file(
            text,
            historical=True,
            scheme_codes={"119551"},
        )

        self.assertEqual([record["scheme_code"] for record in records], ["119551"])

    def test_amfi_historical_report_path_after_nav_resolution(self):
        class FakeResponse:
            text = (
                "Scheme Code;Scheme Name;ISIN Div Payout/ISIN Growth;"
                "ISIN Div Reinvestment;Net Asset Value;Repurchase Price;"
                "Sale Price;Date\n"
                "152075;360 ONE Balanced Hybrid Fund - Regular Plan - Growth;"
                "INF579M01AV5;-;12.9916;12.9916;12.9916;29-Sep-2026\n"
            )
            status_code = 200
            headers = {"Content-Type": "text/plain"}

            def raise_for_status(self):
                return None

        with patch.object(
            AMFIService,
            "_resolve_nav_ids",
            return_value={
                "152075": {
                    "nav_id": "152075",
                    "scheme_name": "360 ONE Balanced Hybrid Fund - Regular Plan - Growth",
                    "match_method": "exact_name",
                    "mf_id": "62",
                }
            },
        ), patch(
            "mutual_funds.services.amfi.requests.get",
            return_value=FakeResponse(),
        ) as mock_get:
            records = AMFIService._download_historical_api_records(
                date(2026, 9, 1),
                date(2026, 9, 29),
                {"152075"},
            )

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["scheme_code"], "152075")
        self.assertEqual(records[0]["nav"], Decimal("12.9916"))
        self.assertEqual(mock_get.call_args.kwargs["params"]["mf"], "62")
        self.assertEqual(mock_get.call_args.kwargs["params"]["frmdt"], "01-Sep-2026")
        self.assertEqual(mock_get.call_args.kwargs["params"]["todt"], "29-Sep-2026")

    def test_current_api_history_resolves_scheme_code_to_nav_id(self):
        class FakeResponse:
            def __init__(self, text):
                self.text = text
                self.status_code = 200
                self.headers = {"Content-Type": "text/plain"}
                self.url = "https://portal.amfiindia.com/DownloadNAVHistoryReport_Po.aspx"

            def raise_for_status(self):
                return None

        report = (
            "Scheme Code;Scheme Name;ISIN Div Payout/ISIN Growth;"
            "ISIN Div Reinvestment;Net Asset Value;Repurchase Price;"
            "Sale Price;Date\n"
            "119551;Requested Fund;INF000000001;-;101.25;101.25;101.25;28-Sep-2026\n"
            "119551;Requested Fund;INF000000001;-;102.50;102.50;102.50;29-Sep-2026\n"
        )

        responses = [
            FakeResponse(report),
        ]
        with patch.object(
            AMFIService,
            "_resolve_nav_ids",
            return_value={
                "119551": {
                    "nav_id": "154043",
                    "scheme_name": "Requested Fund",
                    "match_method": "exact_name",
                    "mf_id": "85",
                }
            },
        ), patch(
            "mutual_funds.services.amfi.requests.get",
            side_effect=responses,
        ) as mock_get:
            records = AMFIService.download_historical_nav(
                date(2026, 9, 1),
                date(2026, 9, 29),
                scheme_codes={"119551"},
            )

        self.assertEqual(len(records), 2)
        self.assertEqual(records[0]["scheme_code"], "119551")
        self.assertEqual(records[0]["nav"], Decimal("101.25"))
        self.assertEqual(records[0]["date"], date(2026, 9, 28))
        self.assertEqual(records[1]["nav"], Decimal("102.50"))

        history_call = mock_get.call_args_list[-1]
        self.assertEqual(history_call.kwargs["params"]["mf"], "85")
        self.assertEqual(history_call.kwargs["params"]["frmdt"], "01-Sep-2026")
        self.assertEqual(history_call.kwargs["params"]["todt"], "29-Sep-2026")

    def test_current_api_history_falls_back_to_explicit_fund_type(self):
        class FakeResponse:
            text = (
                "Scheme Code;Scheme Name;ISIN Div Payout/ISIN Growth;"
                "ISIN Div Reinvestment;Net Asset Value;Repurchase Price;"
                "Sale Price;Date\n"
                "153357;360 ONE Gold ETF - Direct Plan - Growth Option;"
                "-;-;101.25;101.25;101.25;29-Sep-2026\n"
            )
            status_code = 200
            headers = {"Content-Type": "text/plain"}
            url = "https://portal.amfiindia.com/DownloadNAVHistoryReport_Po.aspx"

            def raise_for_status(self):
                return None

        with patch.object(
            AMFIService,
            "_resolve_nav_ids",
            return_value={
                "153357": {
                    "nav_id": "198765",
                    "scheme_name": "360 ONE Gold ETF - Direct Plan - Growth Option",
                    "match_method": "exact_name",
                    "mf_id": "62",
                }
            },
        ), patch(
            "mutual_funds.services.amfi.requests.get",
            return_value=FakeResponse(),
        ) as mock_get:
            records = AMFIService.download_historical_nav(
                date(2026, 9, 1),
                date(2026, 9, 29),
                scheme_codes={"153357"},
            )

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["scheme_code"], "153357")
        self.assertEqual(records[0]["nav"], Decimal("101.25"))
        self.assertEqual(mock_get.call_args.kwargs["params"]["mf"], "62")

    def test_legacy_historical_download_rejects_html(self):
        class FakeResponse:
            text = "<html><body>View/Download NAV History</body></html>"
            status_code = 200
            headers = {"Content-Type": "text/html"}

            def raise_for_status(self):
                return None

        with patch(
            "mutual_funds.services.amfi.requests.get",
            return_value=FakeResponse(),
        ):
            with self.assertRaises(RuntimeError):
                AMFIService.download_historical_nav(
                    date(2026, 9, 1),
                    date(2026, 9, 29),
                )

    def test_historical_report_detection_rejects_html(self):
        self.assertFalse(
            AMFIService._is_historical_report(
                "<html><body>Application Error! Please try again later.</body></html>"
            )
        )


    def test_amfi_resolution_error_is_diagnostic(self):
        with patch(
            "mutual_funds.services.amfi.requests.get",
            side_effect=RuntimeError("api unavailable"),
        ):
            with self.assertRaises(RuntimeError):
                AMFIService._resolve_nav_ids({"999999"})


    def test_amfi_resolution_prefers_isin_over_name(self):
        class R:
            def __init__(self, payload):
                self.payload = payload
            def raise_for_status(self): pass
            def json(self): return self.payload

        AMFIMasterScheme.objects.create(
            scheme_code="152075",
            scheme_name="360 ONE Balanced Hybrid Fund - Regular Plan - Growth",
            isin_growth="INF579M01AV5",
            is_active=True,
        )
        responses = [
            R({"data": [{"mutualFundId": "360", "schemes": [{
                "schemeId": "152075",
                "schemeName": "360 ONE Balanced Hybrid Fund",
            }]}]}),
            R({"data": [
                {"nav_id": "wrong", "nav_name": "360 ONE Balanced Hybrid Fund - Regular Plan - Growth",
                 "MF_ID": "360", "ISIN": "OTHER"},
                {"nav_id": "right", "nav_name": "360 ONE Balanced Hybrid Fund - Regular Plan - Growth Option",
                 "MF_ID": "360", "ISIN": "INF579M01AV5"},
            ]}),
        ]
        with patch("mutual_funds.services.amfi.requests.get", side_effect=responses):
            result = AMFIService._resolve_nav_ids({"152075"})
        self.assertEqual(result["152075"]["nav_id"], "right")
        self.assertEqual(result["152075"]["match_method"], "isin")

    def test_amfi_resolution_rejects_equal_top_candidates(self):
        class R:
            def __init__(self, payload):
                self.payload = payload
            def raise_for_status(self): pass
            def json(self): return self.payload

        AMFIMasterScheme.objects.create(
            scheme_code="123457",
            scheme_name="Ambiguous Fund - Regular Plan - Growth",
            is_active=True,
        )
        responses = [
            R({"data": [{"mutualFundId": "100", "schemes": [{
                "schemeId": "123457", "schemeName": "Ambiguous Fund",
            }]}]}),
            R({"data": [
                {"nav_id": "nav-a", "nav_name": "Ambiguous Fund - Regular Plan - Growth", "MF_ID": "100"},
                {"nav_id": "nav-b", "nav_name": "Ambiguous Fund - Regular Plan - Growth", "MF_ID": "100"},
            ]}),
        ]
        with patch("mutual_funds.services.amfi.requests.get", side_effect=responses):
            with self.assertRaisesRegex(RuntimeError, "AMFI scheme resolution ambiguous: code=123457"):
                AMFIService._resolve_nav_ids({"123457"})
