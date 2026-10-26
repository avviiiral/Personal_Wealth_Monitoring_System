from decimal import Decimal
from datetime import date
from io import BytesIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from openpyxl import load_workbook
from rest_framework.test import APIClient

from investments.models import Asset, AssetCategory, Transaction
from market_data.models import DataSource, MarketPrice
from market_data.services.market_data_manager import MarketDataManager
from mutual_funds.models import (
    MutualFundHolding,
    MutualFundScheme,
    MutualFundTransaction,
    MutualFundTransactionType,
    MutualFundNAV,
)
from users.models import FamilyGroup, Role, TaxRateSetting
from portfolio.mis_report_service import MISReportService


class MISReportAPITests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            username="mis_report_user",
            password="test-password",
        )
        # MIS note mutation tests require an authorized financial writer.
        self.user.profile.role = Role.ADMIN
        self.user.profile.save(update_fields=["role"])
        self.family = FamilyGroup.objects.create(name="MIS Test Family")
        self.user.profile.family_groups.add(self.family)
        self.user.profile.active_family_group = self.family
        self.user.profile.save(update_fields=["active_family_group"])

        self.asset = Asset.objects.create(
            owner=self.user,
            family=self.family,
            name="MIS Equity",
            category="STOCK",
            isin="INE000MISTEST1",
            symbol="MISTEST",
        )
        Transaction.objects.create(
            owner=self.user,
            family=self.family,
            asset=self.asset,
            family_name="DAJ",
            portfolio="Core",
            asset_class="Equity",
            sub_class="Large Cap",
            asset_name="MIS Equity",
            advisors="Advisor A",
            transaction_date=date(2026, 1, 10),
            transaction_type="BUY",
            quantity=Decimal("10"),
            price_per_unit=Decimal("100"),
            amount=Decimal("1000"),
            fees=Decimal("0"),
        )
        MarketPrice.objects.create(
            asset=self.asset,
            date=date.today(),
            close_price=Decimal("125"),
            source=DataSource.MANUAL,
        )

        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        self.reference_rate_patcher = patch.object(
            MISReportService,
            "_reference_rate",
            return_value=(None, None),
        )
        self.bse500_rate_patcher = patch.object(
            MISReportService,
            "_bse500_rate",
            return_value=None,
        )
        self.reference_rate_patcher.start()
        self.bse500_rate_patcher.start()
        self.market_data_patcher = patch.object(
            MarketDataManager,
            "fetch_and_rebuild",
            return_value={"success": False, "skipped": True},
        )
        self.market_data_patcher.start()
        self.addCleanup(self.reference_rate_patcher.stop)
        self.addCleanup(self.bse500_rate_patcher.stop)
        self.addCleanup(self.market_data_patcher.stop)

    def test_mf_historical_nav_falls_back_to_market_price_history(self):
        scheme = MutualFundScheme.objects.create(
            owner=self.user,
            family=self.family,
            scheme_name="MIS Historical Fund",
            scheme_code="MIS-HIST-001",
            isin_growth="INF000MISMF01",
        )
        MutualFundTransaction.objects.create(
            owner=self.user,
            family=self.family,
            family_name="DAJ",
            scheme=scheme,
            transaction_type=MutualFundTransactionType.PURCHASE,
            transaction_date=date(2025, 4, 10),
            units=Decimal("100"),
            nav=Decimal("20"),
            amount=Decimal("2000"),
        )
        history_asset = Asset.objects.create(
            owner=self.user,
            family=self.family,
            name="MIS Historical Fund Price",
            category="MUTUAL_FUND",
            isin="INF000MISMF01",
        )
        MarketPrice.objects.create(
            asset=history_asset,
            date=date(2026, 3, 31),
            close_price=Decimal("27.50"),
            source=DataSource.AMFI,
        )

        value = MISReportService._mf_nav(
            scheme.id,
            date(2026, 3, 31),
            {},
        )

        self.assertEqual(value, Decimal("27.50"))

    def test_report_requires_authentication(self):
        self.client.force_authenticate(user=None)
        response = self.client.get("/api/portfolio/mis-report/")
        self.assertIn(response.status_code, [401, 403])

    def test_custom_date_range_changes_report_dates_and_transaction_period(self):
        response = self.client.get(
            "/api/portfolio/mis-report/",
            {"from_date": "2026-02-01", "to_date": "2026-03-31"},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["period_start"], "2026-02-01")
        self.assertEqual(data["period_end"], "2026-03-31")
        self.assertEqual(data["reporting_date"], "2026-03-31")
        self.assertEqual(data["opening_date"], "2026-01-31")

    def test_custom_date_range_rejects_invalid_dates(self):
        response = self.client.get(
            "/api/portfolio/mis-report/",
            {"from_date": "2026-04-01", "to_date": "2026-03-31"},
        )
        self.assertEqual(response.status_code, 400)

    def test_report_exposes_workbook_sections(self):
        response = self.client.get("/api/portfolio/mis-report/")

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["family_name"], "MIS Test Family")
        self.assertEqual(data["family_names"], ["DAJ"])
        self.assertEqual(len(data["ips"]), 1)
        self.assertEqual(data["ips"][0]["asset_class"], "Equity")
        self.assertEqual(data["data_sheet"][0]["asset_name"], "MIS Equity")
        self.assertEqual(data["data_sheet"][0]["family_name"], "DAJ")
        self.assertEqual(data["data_sheet"][0]["qty_units"], 10.0)
        self.assertEqual(data["data_sheet"][0]["closing_amount"], 1250.0)
        self.assertIn("tax_report", data)
        self.assertEqual(data["tax_report"][0]["realized_pnl"], 0.0)
        self.assertEqual(data["tax_report"][0]["unrealized_pnl"], 250.0)
        self.assertEqual(data["tax_report"][0]["tax_configuration_status"], "MISSING")
        self.assertIsNone(data["tax_report"][0]["realized_tax"])
        self.assertIsNone(data["tax_report"][0]["unrealized_tax"])
        self.assertEqual(data["data_sheet"][0]["advisor"], "Advisor A")
        self.assertEqual(len(data["fund_type_summary"]), 1)
        self.assertEqual(data["fund_type_summary"][0]["fund_type"], "Equity")
        self.assertEqual(data["fund_type_summary"][0]["rows"][0]["fund_name"], "MIS Equity")
        self.assertEqual(data["fund_type_summary"][0]["rows"][0]["total"], 1250.0)
        self.assertEqual(data["fund_type_summary"][0]["subtotal"], 1250.0)
        self.assertIn("notes", data)
        reporting_date = date.today()
        opening_date = MISReportService._prior_month_end(reporting_date)
        self.assertEqual(data["notes"]["title"], f"Notes to MIS {reporting_date.strftime('%B-%Y').upper()}")
        self.assertEqual(data["notes"]["opening_label"], opening_date.strftime("%b-%y").upper())
        self.assertEqual(data["notes"]["closing_label"], reporting_date.strftime("%b-%y").upper())
        self.assertEqual(
            [section["section_number"] for section in data["notes"]["sections"]],
            [1, 2, 3, 4, 5, 6],
        )
        self.assertEqual(
            [item["name"] for item in data["notes"]["sections"][0]["items"]],
            [
                "Mindspace Business Parks",
                "Embassy Office Parks",
                "Brookfield India Real Estate Trust",
                "National Highways Infra Trust",
                "Nexus Select Trust",
                "Knowledge Realty Trust",
                "Bagmane Prime Office Reit",
                "NDR InvIT",
                "Cube InvIT",
            ],
        )
        self.assertEqual(
            [item["name"] for item in data["notes"]["sections"][4]["items"]],
            [
                "NSE",
                "Sterlite Electrical Ltd (Power Transmission)",
                "Sterlite Grid 5 Ltd Unlisted Shares",
            ],
        )

    def test_reference_price_refresh_backfills_previous_month_when_missing(self):
        today = date.today()
        opening_date = MISReportService._prior_month_end(today)
        reference = Asset.objects.create(
            owner=None,
            family=None,
            name="Test MIS Reference",
            category="OTHER",
            symbol="TESTMISREF.NS",
            currency="INR",
            is_active=True,
        )
        # A current price exists, but there is no stored history for the
        # previous month-end used by the Notes opening-rate column.
        MarketPrice.objects.create(
            asset=reference,
            date=today,
            close_price=Decimal("125"),
            source=DataSource.MANUAL,
        )

        with (
            patch.object(
                MISReportService,
                "REFERENCE_SYMBOLS",
                {"Test MIS Reference": "TESTMISREF.NS"},
            ),
            patch.object(
                MISReportService,
                "_custom_note_references",
                return_value={},
            ),
            patch(
                "portfolio.mis_report_service.YahooFinanceService.save_history",
                return_value=1,
            ) as save_history,
        ):
            result = MISReportService.refresh_reference_prices()

        self.assertEqual(result["refreshed"], 1)
        self.assertEqual(result["failed"], 0)
        self.assertEqual(save_history.call_count, 1)
        self.assertLessEqual(
            save_history.call_args.kwargs["start"],
            opening_date,
        )

    def test_notes_ticker_is_persisted_and_drives_live_price_lookup(self):
        opening_date = date(2026, 6, 30)
        as_of = date(2026, 7, 31)
        base_notes = MISReportService._build_notes(
            self.family, [], opening_date, as_of
        )
        editable = MISReportService._editable_notes_from_report(base_notes)
        section = editable["sections"][0]
        row = section["rows"][0]
        row["cells"]["particulars"] = "Custom REIT"
        row["cells"]["symbol"] = "customreit.ns"

        with patch.object(
            MISReportService,
            "_reference_rate",
            return_value=(Decimal("100"), Decimal("125")),
        ) as reference_rate:
            notes, changed = MISReportService.save_editable_notes(
                self.family,
                self.user,
                editable,
                base_notes,
                opening_date=opening_date,
                as_of=as_of,
            )

        self.assertTrue(changed)
        self.assertEqual(
            notes["editable"]["sections"][0]["rows"][0]["cells"]["symbol"],
            "CUSTOMREIT.NS",
        )
        self.assertEqual(
            notes["sections"][0]["items"][0]["opening_rate"],
            Decimal("100"),
        )
        self.assertEqual(
            notes["sections"][0]["items"][0]["closing_rate"],
            Decimal("125"),
        )
        self.assertTrue(any(
            call.kwargs.get("symbol") == "CUSTOMREIT.NS"
            for call in reference_rate.call_args_list
        ))

    def test_tax_report_marks_missing_and_incomplete_tax_settings(self):
        # The fixture's MIS Equity has no tax setting.
        response = self.client.get(
            "/api/portfolio/mis-report/",
            {"from_date": "2026-01-01", "to_date": date.today().isoformat()},
        )
        self.assertEqual(response.status_code, 200)
        row = next(
            item for item in response.json()["tax_report"]
            if item["asset_name"] == "MIS Equity"
        )
        self.assertEqual(row["tax_configuration_status"], "MISSING")
        self.assertIsNone(row["tax_tenure_months"])
        self.assertIsNone(row["short_term_tax_rate"])
        self.assertIsNone(row["long_term_tax_rate"])

        TaxRateSetting.objects.create(
            asset_name="MIS Equity",
            family=None,
            asset=None,
            tenure_months=None,
            short_term_tax_rate=Decimal("0"),
            long_term_tax_rate=Decimal("0"),
        )
        response = self.client.get(
            "/api/portfolio/mis-report/",
            {"from_date": "2026-01-01", "to_date": date.today().isoformat()},
        )
        self.assertEqual(response.status_code, 200)
        row = next(
            item for item in response.json()["tax_report"]
            if item["asset_name"] == "MIS Equity"
        )
        self.assertEqual(row["tax_configuration_status"], "INCOMPLETE")
        self.assertEqual(row["short_term_tax_rate"], 0.0)
        self.assertEqual(row["long_term_tax_rate"], 0.0)
        self.assertIsNone(row["realized_tax"])
        self.assertIsNone(row["unrealized_tax"])

        # An explicitly configured 0% rate is valid and must remain numeric zero.
        setting = TaxRateSetting.objects.get(family__isnull=True, asset__isnull=True, asset_name="MIS Equity")
        setting.tenure_months = 12
        setting.save(update_fields=["tenure_months"])
        response = self.client.get(
            "/api/portfolio/mis-report/",
            {"from_date": "2026-01-01", "to_date": date.today().isoformat()},
        )
        self.assertEqual(response.status_code, 200)
        row = next(
            item for item in response.json()["tax_report"]
            if item["asset_name"] == "MIS Equity"
        )
        self.assertEqual(row["tax_configuration_status"], "CONFIGURED")
        self.assertEqual(row["realized_tax"], 0.0)
        self.assertEqual(row["unrealized_tax"], 0.0)

    def test_tax_report_uses_fifo_and_tenure_based_tax_rates(self):
        fifo_asset = Asset.objects.create(
            owner=self.user,
            family=self.family,
            name="FIFO Equity",
            category="STOCK",
            isin="INE000FIFO1",
            symbol="FIFO1",
        )
        TaxRateSetting.objects.create(
            family=self.family,
            asset=fifo_asset,
            tenure_months=12,
            short_term_tax_rate=Decimal("15"),
            long_term_tax_rate=Decimal("10"),
        )
        Transaction.objects.create(
            owner=self.user,
            family=self.family,
            asset=fifo_asset,
            family_name="DAJ",
            portfolio="Core",
            asset_class="Equity",
            sub_class="Large Cap",
            asset_name="FIFO Equity",
            transaction_date=date(2025, 1, 1),
            transaction_type="BUY",
            quantity=Decimal("10"),
            price_per_unit=Decimal("100"),
            amount=Decimal("1000"),
            fees=Decimal("0"),
        )
        Transaction.objects.create(
            owner=self.user,
            family=self.family,
            asset=fifo_asset,
            family_name="DAJ",
            portfolio="Core",
            asset_class="Equity",
            sub_class="Large Cap",
            asset_name="FIFO Equity",
            transaction_date=date(2026, 1, 1),
            transaction_type="BUY",
            quantity=Decimal("10"),
            price_per_unit=Decimal("200"),
            amount=Decimal("2000"),
            fees=Decimal("0"),
        )
        Transaction.objects.create(
            owner=self.user,
            family=self.family,
            asset=fifo_asset,
            family_name="DAJ",
            portfolio="Core",
            asset_class="Equity",
            sub_class="Large Cap",
            asset_name="FIFO Equity",
            transaction_date=date(2026, 9, 1),
            transaction_type="SELL",
            quantity=Decimal("15"),
            price_per_unit=Decimal("300"),
            amount=Decimal("4500"),
            fees=Decimal("0"),
        )
        MarketPrice.objects.create(
            asset=fifo_asset,
            date=date(2026, 9, 30),
            close_price=Decimal("300"),
            source=DataSource.MANUAL,
        )

        response = self.client.get(
            "/api/portfolio/mis-report/",
            {"from_date": "2026-04-01", "to_date": "2026-09-30"},
        )

        self.assertEqual(response.status_code, 200)
        row = next(item for item in response.json()["tax_report"] if item["asset_name"] == "FIFO Equity")
        self.assertEqual(row["qty_units"], 5.0)
        self.assertEqual(row["realized_pnl"], 2500.0)
        self.assertEqual(row["unrealized_pnl"], 500.0)
        self.assertEqual(row["realized_tax"], 275.0)
        self.assertEqual(row["unrealized_tax"], 75.0)

        Transaction.objects.create(
            owner=self.user,
            family=self.family,
            asset=fifo_asset,
            family_name="DAJ",
            portfolio="Core",
            asset_class="Equity",
            sub_class="Large Cap",
            asset_name="FIFO Equity",
            transaction_date=date(2026, 9, 15),
            transaction_type="BUY",
            quantity=Decimal("5"),
            price_per_unit=Decimal("500"),
            amount=Decimal("2500"),
            fees=Decimal("0"),
        )
        Transaction.objects.create(
            owner=self.user,
            family=self.family,
            asset=fifo_asset,
            family_name="DAJ",
            portfolio="Core",
            asset_class="Equity",
            sub_class="Large Cap",
            asset_name="FIFO Equity",
            transaction_date=date(2026, 9, 20),
            transaction_type="SELL",
            quantity=Decimal("5"),
            price_per_unit=Decimal("100"),
            amount=Decimal("500"),
            fees=Decimal("0"),
        )

        response = self.client.get(
            "/api/portfolio/mis-report/",
            {"from_date": "2026-04-01", "to_date": "2026-09-30"},
        )
        row = next(item for item in response.json()["tax_report"] if item["asset_name"] == "FIFO Equity")
        self.assertEqual(row["realized_pnl"], 2000.0)
        # The September 20 sale is FIFO-matched to the remaining January
        # 2026 lot (cost ₹200), so its ₹500 loss receives the configured
        # 15% short-term rate as a negative tax benefit. Cumulative tax is
        # therefore ₹275 - ₹75 = ₹200.
        self.assertEqual(row["realized_tax"], 200.0)

    def test_tax_is_negative_for_realized_and_unrealized_losses_with_tenure_rate(self):
        loss_asset = Asset.objects.create(
            owner=self.user,
            family=self.family,
            name="Loss Equity",
            category="STOCK",
            isin="INE000LOSS1",
            symbol="LOSS1",
        )
        TaxRateSetting.objects.create(
            family=self.family,
            asset=loss_asset,
            tenure_months=12,
            short_term_tax_rate=Decimal("15"),
            long_term_tax_rate=Decimal("10"),
        )
        Transaction.objects.create(
            owner=self.user,
            family=self.family,
            asset=loss_asset,
            family_name="DAJ",
            portfolio="Core",
            asset_class="Equity",
            sub_class="Large Cap",
            asset_name="Loss Equity",
            transaction_date=date(2026, 1, 10),
            transaction_type="BUY",
            quantity=Decimal("100"),
            price_per_unit=Decimal("100"),
            amount=Decimal("10000"),
            fees=Decimal("0"),
        )
        Transaction.objects.create(
            owner=self.user,
            family=self.family,
            asset=loss_asset,
            family_name="DAJ",
            portfolio="Core",
            asset_class="Equity",
            sub_class="Large Cap",
            asset_name="Loss Equity",
            transaction_date=date(2026, 9, 1),
            transaction_type="SELL",
            quantity=Decimal("50"),
            price_per_unit=Decimal("80"),
            amount=Decimal("4000"),
            fees=Decimal("0"),
        )
        MarketPrice.objects.create(
            asset=loss_asset,
            date=date(2026, 9, 30),
            close_price=Decimal("70"),
            source=DataSource.MANUAL,
        )

        response = self.client.get(
            "/api/portfolio/mis-report/",
            {"from_date": "2026-04-01", "to_date": "2026-09-30"},
        )

        self.assertEqual(response.status_code, 200)
        row = next(
            item for item in response.json()["tax_report"]
            if item["asset_name"] == "Loss Equity"
        )
        self.assertEqual(row["realized_pnl"], -1000.0)
        self.assertEqual(row["realized_tax"], -150.0)
        self.assertEqual(row["unrealized_pnl"], -1500.0)
        self.assertEqual(row["unrealized_tax"], -225.0)

    def test_display_name_tax_setting_is_inherited_by_unconfigured_underlying(self):
        first_asset = Asset.objects.create(
            owner=self.user,
            family=self.family,
            name="Direct Equity",
            category="STOCK",
            isin="INE000DIRECT1",
            symbol="DIRECT1",
        )
        second_asset = Asset.objects.create(
            owner=self.user,
            family=self.family,
            name="Internal Direct Equity Position",
            category="STOCK",
            isin="INE000DIRECT2",
            symbol="DIRECT2",
        )
        TaxRateSetting.objects.create(
            asset_name="Direct Equity",
            family=None,
            asset=None,
            tenure_months=12,
            short_term_tax_rate=Decimal("15"),
            long_term_tax_rate=Decimal("10"),
        )

        for asset, family_name, isin in (
            (first_asset, "DAJ", "INE000DIRECT1"),
            (second_asset, "DJT", "INE000DIRECT2"),
        ):
            Transaction.objects.create(
                owner=self.user,
                family=self.family,
                asset=asset,
                family_name=family_name,
                portfolio="Core",
                asset_class="Equity",
                sub_class="Large Cap",
                asset_name="Direct Equity",
                transaction_date=date(2026, 1, 10),
                transaction_type="BUY",
                quantity=Decimal("10"),
                price_per_unit=Decimal("100"),
                amount=Decimal("1000"),
                fees=Decimal("0"),
            )
            MarketPrice.objects.create(
                asset=asset,
                date=date(2026, 10, 1),
                close_price=Decimal("200"),
                source=DataSource.MANUAL,
            )

        response = self.client.get(
            "/api/portfolio/mis-report/",
            {"from_date": "2026-01-01", "to_date": "2026-10-01"},
        )

        self.assertEqual(response.status_code, 200)
        rows = [
            item for item in response.json()["tax_report"]
            if item["asset_name"] == "Direct Equity"
        ]
        self.assertEqual(len(rows), 2)
        self.assertEqual(
            {item["family_name"] for item in rows},
            {"DAJ", "DJT"},
        )
        rows_by_family = {item["family_name"]: item for item in rows}
        # The global policy for the displayed name governs both rows, even
        # though DAJ and DJT use different underlying Asset records.
        self.assertEqual(rows_by_family["DAJ"]["unrealized_tax"], 150.0)
        self.assertEqual(rows_by_family["DAJ"]["tax_configuration_status"], "CONFIGURED")
        self.assertEqual(rows_by_family["DJT"]["unrealized_tax"], 150.0)
        self.assertEqual(rows_by_family["DJT"]["tax_configuration_status"], "CONFIGURED")

    def test_tax_report_keeps_fully_exited_positions_for_realized_pnl(self):
        exited_asset = Asset.objects.create(
            owner=self.user,
            family=self.family,
            name="Fully Exited Equity",
            category="STOCK",
            isin="INE000EXITED1",
            symbol="EXITED1",
        )
        TaxRateSetting.objects.create(
            family=self.family,
            asset=exited_asset,
            tenure_months=12,
            short_term_tax_rate=Decimal("15"),
            long_term_tax_rate=Decimal("10"),
        )
        Transaction.objects.create(
            owner=self.user,
            family=self.family,
            asset=exited_asset,
            family_name="DAJ",
            portfolio="Core",
            asset_class="Equity",
            sub_class="Large Cap",
            asset_name="Fully Exited Equity",
            transaction_date=date(2026, 1, 10),
            transaction_type="BUY",
            quantity=Decimal("10"),
            price_per_unit=Decimal("100"),
            amount=Decimal("1000"),
            fees=Decimal("0"),
        )
        Transaction.objects.create(
            owner=self.user,
            family=self.family,
            asset=exited_asset,
            family_name="DAJ",
            portfolio="Core",
            asset_class="Equity",
            sub_class="Large Cap",
            asset_name="Fully Exited Equity",
            transaction_date=date(2026, 9, 30),
            transaction_type="SELL",
            quantity=Decimal("10"),
            price_per_unit=Decimal("200"),
            amount=Decimal("2000"),
            fees=Decimal("0"),
        )

        response = self.client.get(
            "/api/portfolio/mis-report/",
            {"from_date": "2026-09-30", "to_date": "2026-10-01"},
        )

        self.assertEqual(response.status_code, 200)
        row = next(
            item
            for item in response.json()["tax_report"]
            if item["asset_name"] == "Fully Exited Equity"
        )
        self.assertEqual(row["qty_units"], 0.0)
        self.assertEqual(row["realized_pnl"], 1000.0)
        self.assertEqual(row["realized_tax"], 150.0)
        self.assertEqual(row["unrealized_pnl"], 0.0)
        self.assertEqual(row["unrealized_tax"], 0.0)

    def test_same_asset_name_is_consolidated_across_positions(self):
        second_asset = Asset.objects.create(
            owner=self.user,
            family=self.family,
            name="MIS Equity Duplicate Position",
            category="STOCK",
            isin="INE000MISTEST2",
            symbol="MISTEST2",
        )
        Transaction.objects.create(
            owner=self.user,
            family=self.family,
            asset=second_asset,
            family_name="DAJ",
            portfolio="Core",
            asset_class="Equity",
            sub_class="Large Cap",
            asset_name="MIS Equity",
            transaction_date=date(2026, 2, 10),
            transaction_type="BUY",
            quantity=Decimal("5"),
            price_per_unit=Decimal("100"),
            amount=Decimal("500"),
            fees=Decimal("0"),
        )
        MarketPrice.objects.create(
            asset=second_asset,
            date=date.today(),
            close_price=Decimal("125"),
            source=DataSource.MANUAL,
        )

        response = self.client.get("/api/portfolio/mis-report/")

        self.assertEqual(response.status_code, 200)
        rows = [row for row in response.json()["data_sheet"] if row["asset_name"] == "MIS Equity"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["qty_units"], 15.0)
        self.assertEqual(rows[0]["total_cost"], 1500.0)
        self.assertEqual(rows[0]["closing_amount"], 1875.0)

    def test_asset_backed_mutual_fund_uses_amfi_nav_when_market_price_is_missing(self):
        """Legacy Asset/Transaction MF rows must use persisted AMFI NAVs."""
        scheme = MutualFundScheme.objects.create(
            owner=self.user,
            family=self.family,
            scheme_name="Legacy Asset MF",
            scheme_code="LEGACY001",
            category="Debt",
            isin_growth="INF000LEGACY1",
        )
        legacy_asset = Asset.objects.create(
            owner=self.user,
            family=self.family,
            name="Legacy Asset MF",
            category=AssetCategory.MUTUAL_FUND,
            isin="INF000LEGACY1",
        )
        Transaction.objects.create(
            owner=self.user,
            family=self.family,
            asset=legacy_asset,
            family_name="DAJ",
            portfolio="Core",
            asset_class="Mutual Funds",
            sub_class="Debt",
            asset_name="Legacy Asset MF",
            transaction_date=date(2026, 1, 10),
            transaction_type="BUY",
            quantity=Decimal("10"),
            price_per_unit=Decimal("100"),
            amount=Decimal("1000"),
            fees=Decimal("0"),
        )
        MutualFundNAV.objects.create(
            scheme=scheme,
            date=date.today(),
            nav=Decimal("123.45"),
            source="AMFI",
        )

        response = self.client.get("/api/portfolio/mis-report/")

        self.assertEqual(response.status_code, 200)
        row = next(
            item for item in response.json()["data_sheet"]
            if item["asset_name"] == "Legacy Asset MF"
        )
        self.assertEqual(row["closing_nav"], 123.45)
        self.assertEqual(row["closing_amount"], 1234.5)

    def test_mutual_fund_data_sheet_and_fund_summary_are_included(self):
        scheme = MutualFundScheme.objects.create(
            owner=self.user,
            family=self.family,
            scheme_name="MIS Equity Fund",
            scheme_code="MIS001",
            category="Equity",
            isin_growth="INF000MISTEST1",
        )
        MutualFundTransaction.objects.create(
            owner=self.user,
            family=self.family,
            family_name="DAJ",
            portfolio="Core",
            scheme=scheme,
            transaction_type=MutualFundTransactionType.PURCHASE,
            transaction_date=date(2026, 1, 15),
            units=Decimal("10"),
            nav=Decimal("100"),
            amount=Decimal("1000"),
            fees=Decimal("0"),
        )
        MutualFundHolding.objects.create(
            owner=self.user,
            family=self.family,
            scheme=scheme,
            units=Decimal("10"),
            invested_value=Decimal("1000"),
            average_nav=Decimal("100"),
            current_nav=Decimal("120"),
            current_value=Decimal("1200"),
            unrealized_pnl=Decimal("200"),
        )
        MutualFundNAV.objects.create(
            scheme=scheme,
            date=date.today(),
            nav=Decimal("120"),
            source="AMFI",
        )

        response = self.client.get("/api/portfolio/mis-report/")
        self.assertEqual(response.status_code, 200)

        data = response.json()
        mf_rows = [row for row in data["data_sheet"] if row["asset_class"] == "Mutual Funds"]
        self.assertEqual(len(mf_rows), 1)
        self.assertEqual(mf_rows[0]["asset_name"], "MIS Equity Fund")
        self.assertEqual(mf_rows[0]["qty_units"], 10.0)
        self.assertEqual(mf_rows[0]["closing_amount"], 1200.0)
        equity_group = next(group for group in data["fund_type_summary"] if group["fund_type"] == "Equity")
        mf_group = next(group for group in data["fund_type_summary"] if group["fund_type"] == "Mutual Funds")
        self.assertEqual(equity_group["rows"][0]["fund_name"], "MIS Equity")
        self.assertEqual(equity_group["rows"][0]["total"], 1250.0)
        self.assertEqual(mf_group["rows"][0]["fund_name"], "MIS Equity Fund")
        self.assertEqual(mf_group["rows"][0]["total"], 1200.0)

    def test_unrelated_family_is_not_accessible(self):
        other_family = FamilyGroup.objects.create(name="Other Family")
        self.user.profile.role = Role.VIEWER
        self.user.profile.family_groups.remove(self.family)
        self.user.profile.active_family_group = other_family
        self.user.profile.save(update_fields=["role", "active_family_group"])

        response = self.client.get("/api/portfolio/mis-report/")
        self.assertEqual(response.status_code, 403)

    def test_empty_family_returns_empty_sections(self):
        empty_family = FamilyGroup.objects.create(name="Empty MIS Family")
        self.user.profile.family_groups.add(empty_family)
        self.user.profile.active_family_group = empty_family
        self.user.profile.save(update_fields=["active_family_group"])

        response = self.client.get("/api/portfolio/mis-report/")
        self.assertEqual(response.status_code, 200)

        data = response.json()
        self.assertEqual(data["ips"], [])
        self.assertEqual(data["data_sheet"], [])
        self.assertEqual(data["fund_type_summary"], [])
        self.assertEqual(data["tax_report"], [])

    def test_excel_download_contains_three_workbook_sheets_in_template_order(self):
        response = self.client.get("/api/portfolio/mis-report/download/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response["Content-Type"],
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

        workbook = load_workbook(BytesIO(response.content), data_only=False)
        self.assertEqual(
            workbook.sheetnames,
            ["IPS", "Data Sheet", "Tax Report", "Fund Type Summary", "Notes"],
        )
        self.assertEqual(workbook["IPS"]["A1"].value, "IPS")
        self.assertIn("Blank tax amounts mean", workbook["Tax Report"]["A2"].value)
        reporting_date = date.today()
        opening_date = MISReportService._prior_month_end(reporting_date)
        period_start = MISReportService._period_start(reporting_date)
        self.assertEqual(workbook["Notes"]["A1"].value, f"Notes to MIS {reporting_date.strftime('%B-%Y').upper()}")
        self.assertEqual(workbook["Data Sheet"]["A1"].value, "Data Sheet")
        self.assertEqual(workbook["Data Sheet"]["E3"].value, f"Investment Cost {reporting_date.strftime('%d.%m.%Y')}")
        self.assertEqual(workbook["Data Sheet"]["H3"].value, f"{opening_date.strftime('%b-%y').upper()} Closing MTM")
        self.assertEqual(workbook["Data Sheet"]["K3"].value, f"Transactions- Buy/Sell {period_start.strftime('%d.%m.%Y')} to {reporting_date.strftime('%d.%m.%Y')}")
        self.assertEqual(workbook["Data Sheet"]["N3"].value, f"{reporting_date.strftime('%b-%y').upper()} Closing MTM")
        self.assertEqual(workbook["Data Sheet"]["A4"].value, "Fund Name")
        self.assertEqual(workbook["Data Sheet"]["B5"].value, "DAJ")
        self.assertEqual(workbook["Data Sheet"]["A5"].value, "MIS Equity")
        self.assertEqual(workbook["Data Sheet"]["A2"].value, None)
        self.assertEqual(workbook["Data Sheet"]["A6"].value, None)
        # Missing settings are visibly marked and tax amounts remain blank, not zero.
        tax_sheet = workbook["Tax Report"]
        self.assertEqual(tax_sheet["U5"].value, "MISSING")
        self.assertIsNone(tax_sheet["S5"].value)
        self.assertIsNone(tax_sheet["T5"].value)
        tax_ws = workbook["Tax Report"]
        self.assertEqual(tax_ws["A1"].value, "Tax Report")
        self.assertEqual(tax_ws["Q3"].value, "Taxation")
        self.assertEqual(tax_ws["Q4"].value, "Realized P/L")
        self.assertEqual(tax_ws["R4"].value, "Unrealized P/L")
        self.assertEqual(tax_ws["S4"].value, "Realized Tax")
        self.assertEqual(tax_ws["T4"].value, "Unrealized Tax")
        self.assertEqual(tax_ws["U3"].value, "Tax Configuration")
        self.assertEqual(tax_ws["U4"].value, "Tax Configuration Status")
        self.assertEqual(tax_ws["V4"].value, "Tax Tenure (Months)")
        self.assertEqual(tax_ws["W4"].value, "Short-Term Tax Rate (%)")
        self.assertEqual(tax_ws["X4"].value, "Long-Term Tax Rate (%)")
        self.assertEqual(tax_ws["U5"].value, "MISSING")
        self.assertIsNone(tax_ws["V5"].value)
        self.assertIsNone(tax_ws["W5"].value)
        self.assertIsNone(tax_ws["X5"].value)
        self.assertEqual(tax_ws["A5"].value, "MIS Equity")
        fund_summary_ws = workbook["Fund Type Summary"]
        self.assertEqual(fund_summary_ws["A1"].value, "Fund Type wise Summary")
        self.assertEqual(fund_summary_ws["A2"].value, "Values in ₹ Lakhs")
        self.assertEqual(fund_summary_ws["A3"].value, "Fund Type.V2")
        self.assertEqual(fund_summary_ws["B3"].value, "Fund Name")
        self.assertEqual(fund_summary_ws["C3"].value, "Total")
        self.assertEqual(fund_summary_ws["A4"].value, "Asset class")
        self.assertEqual(fund_summary_ws["B4"].value, "Asset name")
        self.assertEqual(fund_summary_ws["C4"].value, "Current Market Value")
        self.assertEqual(fund_summary_ws["A5"].value, "Equity")
        self.assertEqual(fund_summary_ws["B5"].value, "MIS Equity")
        self.assertAlmostEqual(fund_summary_ws["C5"].value, 0.0125, places=8)
        self.assertEqual(fund_summary_ws.max_column, 3)
        self.assertEqual(workbook["IPS"]["A2"].value, "Values in ₹ Lakhs")
        self.assertEqual(workbook["IPS"]["C4"].value, 0.0125)
        self.assertEqual(workbook["IPS"]["D4"].value, 0.0125)
        self.assertEqual(workbook["IPS"]["A7"].value, "Grand Total")
        notes_ws = workbook["Notes"]
        self.assertEqual(notes_ws["A3"].value, 1)
        self.assertEqual(notes_ws["B3"].value, "REITS Rate movement are as below:")
        self.assertEqual(notes_ws["A5"].value, 1)
        self.assertEqual(notes_ws["B5"].value, "Mindspace Business Parks")
        self.assertEqual(notes_ws["A15"].value, 2)
        self.assertEqual(notes_ws["B15"].value, "Sovereign Gold Bonds rate movement are as below:")
        self.assertEqual(workbook["IPS"]["C7"].value, 0.0125)

    def test_fund_type_summary_groups_all_asset_classes_by_current_market_value(self):
        response = self.client.get("/api/portfolio/mis-report/")
        self.assertEqual(response.status_code, 200)
        data = response.json()

        groups = {group["fund_type"]: group for group in data["fund_type_summary"]}
        self.assertIn("Equity", groups)
        self.assertEqual(groups["Equity"]["rows"][0]["fund_name"], "MIS Equity")
        self.assertEqual(groups["Equity"]["rows"][0]["total"], 1250.0)

    def test_excel_download_supports_amount_and_crores_display_units(self):
        for unit, expected_label, expected_value in [
            ("amount", "Values in ₹ Amount", 1250.0),
            ("crores", "Values in ₹ Crores", 0.000125),
        ]:
            response = self.client.get("/api/portfolio/mis-report/download/", {"display_unit": unit})
            self.assertEqual(response.status_code, 200)
            workbook = load_workbook(BytesIO(response.content), data_only=False)
            self.assertEqual(workbook["IPS"]["A2"].value, expected_label)
            self.assertAlmostEqual(workbook["IPS"]["C4"].value, expected_value, places=8)

    def test_invalid_excel_display_unit_is_rejected(self):
        response = self.client.get("/api/portfolio/mis-report/download/", {"display_unit": "millions"})
        self.assertEqual(response.status_code, 400)


    def test_saved_note_tracks_current_asset_price_regardless_of_section(self):
        gold_etf = Asset.objects.create(
            owner=self.user,
            family=self.family,
            name="ICICI Prudential Gold ETF",
            category="ETF",
            isin="INE123GOLDETF1",
            symbol="GOLDIETF",
        )
        MarketPrice.objects.create(
            asset=gold_etf,
            date=date.today(),
            close_price=Decimal("125.50"),
            source=DataSource.MANUAL,
        )

        response = self.client.get("/api/portfolio/mis-report/")
        self.assertEqual(response.status_code, 200)
        document = response.json()["notes"]["editable"]
        silver_section = next(
            section for section in document["sections"]
            if section["id"] == "silver"
        )
        silver_section["rows"].append({
            "id": "gold-etf-row",
            "cells": {
                "sr_no": 99,
                "particulars": "ICICI Prudential Gold ETF",
                "opening_rate": None,
                "closing_rate": None,
                "change": None,
                "percent_change": None,
            },
        })

        response = self.client.put(
            "/api/portfolio/mis-report/notes/",
            {"notes": document, "auto_fill": False},
            format="json",
        )
        self.assertEqual(response.status_code, 200)

        saved = response.json()["notes"]["editable"]
        saved_row = next(
            row
            for row in next(
                section for section in saved["sections"]
                if section["id"] == "silver"
            )["rows"]
            if row["id"] == "gold-etf-row"
        )
        self.assertEqual(saved_row["cells"]["closing_rate"], 125.50)

        # The stored Notes document must not freeze the old price. A later
        # market-price refresh should immediately flow through to the same row.
        MarketPrice.objects.update_or_create(
            asset=gold_etf,
            date=date.today(),
            source=DataSource.MANUAL,
            defaults={"close_price": Decimal("131.25")},
        )
        response = self.client.get("/api/portfolio/mis-report/")
        self.assertEqual(response.status_code, 200)
        current = response.json()["notes"]["editable"]
        current_row = next(
            row
            for row in next(
                section for section in current["sections"]
                if section["id"] == "silver"
            )["rows"]
            if row["id"] == "gold-etf-row"
        )
        self.assertEqual(current_row["cells"]["closing_rate"], 131.25)

    def test_removing_note_row_stops_note_price_tracking(self):
        response = self.client.get("/api/portfolio/mis-report/")
        self.assertEqual(response.status_code, 200)
        document = response.json()["notes"]["editable"]
        section = document["sections"][0]
        section["rows"].append({
            "id": "tracked-row",
            "cells": {
                "sr_no": 99,
                "particulars": "Tracked Note Equity",
                "opening_rate": None,
                "closing_rate": None,
                "change": None,
                "percent_change": None,
            },
        })

        response = self.client.put(
            "/api/portfolio/mis-report/notes/",
            {"notes": document, "auto_fill": False},
            format="json",
        )
        self.assertEqual(response.status_code, 200)

        document = response.json()["notes"]["editable"]
        section = document["sections"][0]
        section["rows"] = [
            row for row in section["rows"]
            if row["id"] != "tracked-row"
        ]

        response = self.client.put(
            "/api/portfolio/mis-report/notes/",
            {"notes": document, "auto_fill": False},
            format="json",
        )
        self.assertEqual(response.status_code, 200)

        saved = response.json()["notes"]["editable"]
        all_names = [
            row["cells"].get("particulars")
            for section in saved["sections"]
            for row in section["rows"]
        ]
        self.assertNotIn("Tracked Note Equity", all_names)

    def test_notes_can_be_edited_and_are_family_scoped(self):
        response = self.client.get("/api/portfolio/mis-report/")
        self.assertEqual(response.status_code, 200)
        document = response.json()["notes"]["editable"]

        section = document["sections"][0]
        section["title"] = "Custom REIT Section"
        section["columns"][1]["label"] = "Investment Name"
        section["columns"].append({
            "id": "custom-value",
            "label": "Custom Value",
            "type": "number",
        })
        section["rows"][0]["cells"]["custom-value"] = 123.45

        response = self.client.put(
            "/api/portfolio/mis-report/notes/",
            {"notes": document, "auto_fill": False},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["changed"])

        response = self.client.get("/api/portfolio/mis-report/")
        self.assertEqual(response.status_code, 200)
        saved = response.json()["notes"]["editable"]
        self.assertEqual(saved["sections"][0]["title"], "Custom REIT Section")
        self.assertEqual(saved["sections"][0]["columns"][1]["label"], "Investment Name")
        self.assertEqual(saved["sections"][0]["rows"][0]["cells"]["custom-value"], 123.45)

        history = self.client.get("/api/portfolio/mis-report/notes/history/")
        self.assertEqual(history.status_code, 200)
        self.assertGreaterEqual(history.json()["count"], 1)
        self.assertEqual(history.json()["results"][0]["user"], self.user.username)

    def test_new_note_row_can_auto_fill_from_existing_asset(self):
        response = self.client.get("/api/portfolio/mis-report/")
        self.assertEqual(response.status_code, 200)
        document = response.json()["notes"]["editable"]

        section = document["sections"][0]
        section["rows"].append({
            "id": "new-row",
            "cells": {
                "sr_no": 99,
                "particulars": "MIS Equity",
                "opening_rate": None,
                "closing_rate": None,
                "change": None,
                "percent_change": None,
            },
        })

        response = self.client.put(
            "/api/portfolio/mis-report/notes/",
            {"notes": document, "auto_fill": True},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        row = next(
            item for item in response.json()["notes"]["editable"]["sections"][0]["rows"]
            if item["id"] == "new-row"
        )
        self.assertIsNone(row["cells"]["opening_rate"])
        self.assertEqual(row["cells"]["closing_rate"], 125.0)

    def test_notes_edits_are_not_visible_to_another_family(self):
        other_family = FamilyGroup.objects.create(name="Other MIS Family")
        other_user = get_user_model().objects.create_user(
            username="other_mis_user",
            password="test-password",
        )
        other_user.profile.family_groups.add(other_family)
        other_user.profile.active_family_group = other_family
        other_user.profile.save(update_fields=["active_family_group"])

        response = self.client.get("/api/portfolio/mis-report/")
        document = response.json()["notes"]["editable"]
        document["title"] = "Family One Custom Notes"
        self.client.put(
            "/api/portfolio/mis-report/notes/",
            {"notes": document},
            format="json",
        )

        other_client = APIClient()
        other_client.force_authenticate(user=other_user)
        response = other_client.get("/api/portfolio/mis-report/")
        self.assertEqual(response.status_code, 200)
        self.assertNotEqual(
            response.json()["notes"]["title"],
            "Family One Custom Notes",
        )

    def test_tax_estimate_applies_configured_rates_to_losses(self):
        from datetime import date
        from decimal import Decimal

        asset = Asset.objects.create(
            owner=self.user, family=self.family, name="Loss Position",
            category="STOCK", symbol="LOSSPOS",
        )
        common = {
            "owner": self.user, "family": self.family, "family_name": "DAJ",
            "portfolio": "Core", "asset_class": "Equity",
            "sub_class": "Large Cap", "asset_name": "Loss Position",
            "advisors": "Advisor A", "fees": Decimal("0"), "asset": asset,
        }
        buy = Transaction.objects.create(
            **common, transaction_date=date(2025, 1, 1),
            transaction_type="BUY", quantity=Decimal("10"),
            price_per_unit=Decimal("100"), amount=Decimal("1000"),
        )
        sell = Transaction.objects.create(
            **common, transaction_date=date(2025, 6, 1),
            transaction_type="SELL", quantity=Decimal("5"),
            price_per_unit=Decimal("80"), amount=Decimal("400"),
        )
        setting = TaxRateSetting.objects.create(
            family=self.family, asset=asset, tenure_months=12,
            short_term_tax_rate=Decimal("23.73"),
            long_term_tax_rate=Decimal("14.95"),
        )

        metrics = MISReportService._fifo_tax_metrics(
            [buy, sell], "asset", date(2025, 12, 31), date(2025, 1, 1),
            setting, {asset.id: Decimal("80")},
        )

        self.assertEqual(metrics["realized_pnl"], Decimal("-100"))
        self.assertEqual(metrics["realized_tax"], Decimal("-23.7300"))
        self.assertEqual(metrics["unrealized_pnl"], Decimal("-100"))
        self.assertEqual(metrics["unrealized_tax"], Decimal("-23.7300"))

    def test_hdfc_focused_fund_tax_matches_october_2026_transaction_amounts(self):
        """Regression for the supplied HDFC DAJ/DJT quantities, costs and tax rates."""
        from datetime import date
        from decimal import Decimal

        fund = Asset.objects.create(
            owner=self.user,
            family=self.family,
            name="HDFC Focused Fund - Growth Option - Direct Plan",
            category="MUTUAL_FUND",
            isin="INF179K01VK7",
            symbol="HDFCFOCUSED",
        )
        report_date = date(2026, 10, 12)
        MarketPrice.objects.create(
            asset=fund,
            date=report_date,
            close_price=Decimal("256.104"),
            # Legacy asset-backed MF holdings resolve NAV from AMFI price history.
            source="AMFI",
        )

        common = {
            "owner": self.user,
            "family": self.family,
            "asset": fund,
            "asset_class": "Equity",
            "sub_class": "Equity Mutual Fund",
            "asset_name": "HDFC Focused Fund - Growth Option - Direct Plan",
            "advisors": "ARTHYA",
            "portfolio": "Equity MF",
            "fees": Decimal("0"),
        }

        # DAJ has two lots straddling the configured 12-month tenure boundary.
        Transaction.objects.create(
            **common,
            family_name="DAJ",
            transaction_date=date(2025, 10, 7),
            transaction_type="BUY",
            quantity=Decimal("5091.381"),
            price_per_unit=Decimal("267.69"),
            amount=Decimal("1362979.88"),
        )
        Transaction.objects.create(
            **common,
            family_name="DAJ",
            transaction_date=date(2025, 10, 15),
            transaction_type="BUY",
            quantity=Decimal("25771.215"),
            price_per_unit=Decimal("268.795"),
            amount=Decimal("6927520.17"),
        )

        # All 35 DJT source lots are long-term as of 12 Oct 2026. Aggregating
        # them into one test lot preserves the same tax result because every
        # lot uses the configured 14.95% long-term rate and there are no sales.
        Transaction.objects.create(
            **common,
            family_name="DJT",
            transaction_date=date(2023, 4, 11),
            transaction_type="BUY",
            quantity=Decimal("242899.529"),
            price_per_unit=Decimal("223.66"),
            amount=Decimal("54327648.60"),
        )

        TaxRateSetting.objects.create(
            asset_name="HDFC Focused Fund - Growth Option - Direct Plan",
            family=None,
            asset=None,
            tenure_months=12,
            short_term_tax_rate=Decimal("23.73"),
            long_term_tax_rate=Decimal("14.95"),
        )

        response = self.client.get(
            "/api/portfolio/mis-report/",
            {"from_date": "2026-04-01", "to_date": "2026-10-12"},
        )
        self.assertEqual(response.status_code, 200)
        tax_rows = response.json()["tax_report"]
        daj = next(
            row for row in tax_rows
            if row["asset_name"] == "HDFC Focused Fund - Growth Option - Direct Plan"
            and row["family_name"] == "DAJ"
        )
        djt = next(
            row for row in tax_rows
            if row["asset_name"] == "HDFC Focused Fund - Growth Option - Direct Plan"
            and row["family_name"] == "DJT"
        )

        self.assertAlmostEqual(daj["qty_units"], 30862.596, places=3)
        self.assertAlmostEqual(daj["total_cost"], 8290500.05, places=2)
        self.assertAlmostEqual(daj["closing_amount"], 7904034.285984, places=2)
        self.assertAlmostEqual(daj["realized_tax"], 0.0, places=2)
        self.assertAlmostEqual(daj["unrealized_pnl"], -386465.764016, places=2)
        self.assertAlmostEqual(daj["unrealized_tax"], -86523.135215984, places=2)

        self.assertAlmostEqual(djt["qty_units"], 242899.529, places=3)
        self.assertAlmostEqual(djt["total_cost"], 54327648.60, places=2)
        self.assertAlmostEqual(djt["closing_amount"], 62207540.975016, places=2)
        self.assertAlmostEqual(djt["realized_tax"], 0.0, places=2)
        self.assertAlmostEqual(djt["unrealized_pnl"], 7879892.375016, places=2)
        self.assertAlmostEqual(djt["unrealized_tax"], 1178043.910064892, places=2)

    def test_fifo_tax_uses_tax_setting_for_each_underlying_position(self):
        asset_a = Asset.objects.create(
            owner=self.user, family=self.family, name="Long Term Underlying",
            category="STOCK", symbol="LONGTERM",
        )
        asset_b = Asset.objects.create(
            owner=self.user, family=self.family, name="Short Term Underlying",
            category="STOCK", symbol="SHORTTERM",
        )
        common = {
            "owner": self.user,
            "family": self.family,
            "family_name": "DAJ",
            "portfolio": "Grouped PMS",
            "asset_class": "Equity",
            "sub_class": "Equity PMS",
            "asset_name": "Grouped PMS",
            "advisors": "Advisor A",
            "fees": Decimal("0"),
        }
        transactions = [
            Transaction.objects.create(
                **common, asset=asset_a, transaction_date=date(2024, 1, 1),
                transaction_type="BUY", quantity=Decimal("10"),
                price_per_unit=Decimal("100"), amount=Decimal("1000"),
            ),
            Transaction.objects.create(
                **common, asset=asset_b, transaction_date=date(2026, 8, 1),
                transaction_type="BUY", quantity=Decimal("10"),
                price_per_unit=Decimal("100"), amount=Decimal("1000"),
            ),
            Transaction.objects.create(
                **common, asset=asset_a, transaction_date=date(2026, 9, 1),
                transaction_type="SELL", quantity=Decimal("5"),
                price_per_unit=Decimal("110"), amount=Decimal("550"),
            ),
            Transaction.objects.create(
                **common, asset=asset_b, transaction_date=date(2026, 9, 1),
                transaction_type="SELL", quantity=Decimal("5"),
                price_per_unit=Decimal("110"), amount=Decimal("550"),
            ),
        ]
        long_term_setting = TaxRateSetting.objects.create(
            family=self.family, asset=asset_a, tenure_months=12,
            short_term_tax_rate=Decimal("25"),
            long_term_tax_rate=Decimal("10"),
        )
        short_term_setting = TaxRateSetting.objects.create(
            family=self.family, asset=asset_b, tenure_months=12,
            short_term_tax_rate=Decimal("20"),
            long_term_tax_rate=Decimal("8"),
        )

        metrics = MISReportService._fifo_tax_metrics(
            transactions,
            "asset",
            date(2026, 10, 1),
            date(2026, 9, 1),
            {
                asset_a.id: long_term_setting,
                asset_b.id: short_term_setting,
            },
            {
                asset_a.id: Decimal("120"),
                asset_b.id: Decimal("120"),
            },
        )

        # Realized gain is ₹50 per asset: A uses its 10% LT rate and B
        # uses its 20% ST rate, yielding ₹5 + ₹10 tax.
        self.assertEqual(metrics["realized_pnl"], Decimal("100"))
        self.assertEqual(metrics["realized_tax"], Decimal("15.00"))
        # Each has five units left and ₹100 unrealized gain. Their own rates
        # produce ₹10 + ₹20 tax; one row-level rate would be incorrect.
        self.assertEqual(metrics["unrealized_pnl"], Decimal("200"))
        self.assertEqual(metrics["unrealized_tax"], Decimal("30.00"))

    def test_fifo_tax_matches_sales_to_the_same_underlying_asset(self):
        from datetime import date
        from decimal import Decimal

        asset_a = Asset.objects.create(
            owner=self.user, family=self.family, name="Shared Display A",
            category="STOCK", symbol="SHAREDA",
        )
        asset_b = Asset.objects.create(
            owner=self.user, family=self.family, name="Shared Display B",
            category="STOCK", symbol="SHAREDB",
        )
        common = {
            "owner": self.user,
            "family": self.family,
            "family_name": "DAJ",
            "portfolio": "Core",
            "asset_class": "Equity",
            "sub_class": "Large Cap",
            "asset_name": "Shared Display",
            "advisors": "Advisor A",
            "fees": Decimal("0"),
        }
        buy_a = Transaction.objects.create(
            **common, asset=asset_a, transaction_date=date(2025, 1, 1),
            transaction_type="BUY", quantity=Decimal("10"),
            price_per_unit=Decimal("100"), amount=Decimal("1000"),
        )
        buy_b = Transaction.objects.create(
            **common, asset=asset_b, transaction_date=date(2025, 1, 2),
            transaction_type="BUY", quantity=Decimal("10"),
            price_per_unit=Decimal("500"), amount=Decimal("5000"),
        )
        sell_b = Transaction.objects.create(
            **common, asset=asset_b, transaction_date=date(2026, 1, 2),
            transaction_type="SELL", quantity=Decimal("10"),
            price_per_unit=Decimal("600"), amount=Decimal("6000"),
        )
        setting = TaxRateSetting.objects.create(
            family=self.family, asset=asset_a, tenure_months=12,
            short_term_tax_rate=Decimal("15"),
            long_term_tax_rate=Decimal("10"),
        )

        metrics = MISReportService._fifo_tax_metrics(
            [buy_a, buy_b, sell_b],
            "asset",
            date(2026, 2, 1),
            date(2026, 1, 1),
            setting,
            {asset_a.id: Decimal("120"), asset_b.id: None},
        )

        # Selling asset B must consume B's ₹5,000 cost lot, not asset A's
        # cheaper ₹1,000 lot merely because both share one display name.
        self.assertEqual(metrics["realized_pnl"], Decimal("1000"))
        self.assertEqual(metrics["realized_tax"], Decimal("100"))
        # Asset A remains open and is valued at its own ₹120 market price.
        self.assertEqual(metrics["unrealized_pnl"], Decimal("200"))
        self.assertEqual(metrics["unrealized_tax"], Decimal("20"))



    def test_display_asset_name_tax_setting_overrides_all_underlying_settings(self):
        """A displayed PMS tax entry governs every underlying in its row."""
        older_underlying = Asset.objects.create(
            owner=self.user,
            family=self.family,
            name="Grouped PMS Underlying A",
            category="STOCK",
            symbol="GROUPA",
        )
        newer_underlying = Asset.objects.create(
            owner=self.user,
            family=self.family,
            name="Grouped PMS Underlying B",
            category="STOCK",
            symbol="GROUPB",
        )
        common = {
            "owner": self.user,
            "family": self.family,
            "family_name": "DAJ",
            "portfolio": "Grouped PMS Portfolio",
            "asset_class": "Equity",
            "sub_class": "Equity PMS",
            "asset_name": "Grouped PMS",
            "advisors": "Advisor A",
            "fees": Decimal("0"),
        }
        Transaction.objects.create(
            **common,
            asset=older_underlying,
            transaction_date=date(2024, 1, 1),
            transaction_type="BUY",
            quantity=Decimal("10"),
            price_per_unit=Decimal("100"),
            amount=Decimal("1000"),
        )
        Transaction.objects.create(
            **common,
            asset=newer_underlying,
            transaction_date=date(2026, 9, 1),
            transaction_type="BUY",
            quantity=Decimal("10"),
            price_per_unit=Decimal("100"),
            amount=Decimal("1000"),
        )
        for underlying in (older_underlying, newer_underlying):
            MarketPrice.objects.create(
                asset=underlying,
                date=date.today(),
                close_price=Decimal("120"),
                source=DataSource.MANUAL,
            )

        # Deliberately conflicting underlying settings prove that the display
        # Asset Name policy takes precedence. All rates come from database rows.
        TaxRateSetting.objects.create(
            asset_name="Grouped PMS",
            family=None,
            asset=None,
            tenure_months=12,
            short_term_tax_rate=Decimal("10"),
            long_term_tax_rate=Decimal("8"),
        )
        TaxRateSetting.objects.create(
            family=self.family,
            asset=older_underlying,
            tenure_months=12,
            short_term_tax_rate=Decimal("25"),
            long_term_tax_rate=Decimal("20"),
        )

        response = self.client.get("/api/portfolio/mis-report/")
        self.assertEqual(response.status_code, 200)
        row = next(
            item for item in response.json()["tax_report"]
            if item["asset_name"] == "Grouped PMS" and item["family_name"] == "DAJ"
        )

        # Older lot uses the display policy's 8% LT rate; newer lot uses its
        # 10% ST rate. Each security retains its own FIFO lots and valuation.
        self.assertEqual(row["tax_configuration_status"], "CONFIGURED")
        self.assertEqual(row["tax_tenure_months"], 12)
        self.assertEqual(row["short_term_tax_rate"], 10.0)
        self.assertEqual(row["long_term_tax_rate"], 8.0)
        self.assertAlmostEqual(row["unrealized_pnl"], 400.0, places=2)
        self.assertAlmostEqual(row["unrealized_tax"], 36.0, places=2)

    def test_global_display_policy_keeps_underlying_fifo_lots_separate(self):
        older_asset = Asset.objects.create(
            owner=self.user,
            family=self.family,
            name="Global Group Underlying A",
            category="STOCK",
            symbol="GLOBALGROUPA",
        )
        recent_asset = Asset.objects.create(
            owner=self.user,
            family=self.family,
            name="Global Group Underlying B",
            category="STOCK",
            symbol="GLOBALGROUPB",
        )
        common = {
            "owner": self.user,
            "family": self.family,
            "family_name": "DAJ",
            "portfolio": "Global Group Portfolio",
            "asset_class": "Equity",
            "sub_class": "Equity PMS",
            "asset_name": "Global Group",
            "advisors": "Advisor A",
            "fees": Decimal("0"),
        }
        Transaction.objects.create(
            **common,
            asset=older_asset,
            transaction_date=date(2024, 1, 1),
            transaction_type="BUY",
            quantity=Decimal("10"),
            price_per_unit=Decimal("100"),
            amount=Decimal("1000"),
        )
        Transaction.objects.create(
            **common,
            asset=recent_asset,
            transaction_date=date(2026, 8, 1),
            transaction_type="BUY",
            quantity=Decimal("10"),
            price_per_unit=Decimal("500"),
            amount=Decimal("5000"),
        )
        Transaction.objects.create(
            **common,
            asset=recent_asset,
            transaction_date=date(2026, 10, 1),
            transaction_type="SELL",
            quantity=Decimal("10"),
            price_per_unit=Decimal("600"),
            amount=Decimal("6000"),
        )
        for asset in (older_asset, recent_asset):
            MarketPrice.objects.create(
                asset=asset,
                date=date.today(),
                close_price=Decimal("120"),
                source=DataSource.MANUAL,
            )
        TaxRateSetting.objects.create(
            asset_name="Global Group",
            family=None,
            asset=None,
            tenure_months=12,
            short_term_tax_rate=Decimal("20"),
            long_term_tax_rate=Decimal("8"),
        )

        response = self.client.get(
            "/api/portfolio/mis-report/",
            {"from_date": "2026-10-01", "to_date": date.today().isoformat()},
        )
        self.assertEqual(response.status_code, 200)
        row = next(
            item for item in response.json()["tax_report"]
            if item["asset_name"] == "Global Group" and item["family_name"] == "DAJ"
        )

        # The sale of B realizes only B's ₹1,000 gain at the 20% short-term
        # rate. A's 10-unit lot remains open at its own cost basis and gets
        # the 8% long-term rate on its ₹200 unrealized gain.
        self.assertEqual(row["tax_configuration_status"], "CONFIGURED")
        self.assertEqual(row["realized_pnl"], 1000.0)
        self.assertEqual(row["realized_tax"], 200.0)
        self.assertEqual(row["unrealized_pnl"], 200.0)
        self.assertEqual(row["unrealized_tax"], 16.0)


    def test_fifo_lots_are_isolated_by_family_member_and_portfolio(self):
        member_a_buy = Transaction.objects.create(
            owner=self.user,
            family=self.family,
            family_name="Member A",
            portfolio="Portfolio A",
            asset_class="Equity",
            sub_class="Direct Equity",
            asset_name="Shared Holding",
            underlying="Shared Holding",
            advisors="Advisor",
            asset=self.asset,
            transaction_date=date(2025, 1, 1),
            transaction_type="BUY",
            quantity=Decimal("10"),
            price_per_unit=Decimal("100"),
            amount=Decimal("1000"),
            fees=Decimal("0"),
        )
        member_b_buy = Transaction.objects.create(
            owner=self.user,
            family=self.family,
            family_name="Member B",
            portfolio="Portfolio B",
            asset_class="Equity",
            sub_class="Direct Equity",
            asset_name="Shared Holding",
            underlying="Shared Holding",
            advisors="Advisor",
            asset=self.asset,
            transaction_date=date(2025, 1, 2),
            transaction_type="BUY",
            quantity=Decimal("10"),
            price_per_unit=Decimal("500"),
            amount=Decimal("5000"),
            fees=Decimal("0"),
        )
        member_b_sell = Transaction.objects.create(
            owner=self.user,
            family=self.family,
            family_name="Member B",
            portfolio="Portfolio B",
            asset_class="Equity",
            sub_class="Direct Equity",
            asset_name="Shared Holding",
            underlying="Shared Holding",
            advisors="Advisor",
            asset=self.asset,
            transaction_date=date(2026, 1, 2),
            transaction_type="SELL",
            quantity=Decimal("10"),
            price_per_unit=Decimal("600"),
            amount=Decimal("6000"),
            fees=Decimal("0"),
        )
        setting = TaxRateSetting.objects.create(
            family=self.family,
            asset=self.asset,
            tenure_months=12,
            short_term_tax_rate=Decimal("15"),
            long_term_tax_rate=Decimal("10"),
        )

        metrics = MISReportService._fifo_tax_metrics(
            [member_a_buy, member_b_buy, member_b_sell],
            "asset",
            date(2026, 2, 1),
            date(2026, 1, 1),
            {self.asset.id: setting},
            {self.asset.id: Decimal("600")},
        )

        # Member B's sale must consume B's ₹5,000 lot, not Member A's ₹1,000
        # lot, even though the database Asset FK is the same.
        self.assertEqual(metrics["realized_pnl"], Decimal("1000"))
        self.assertEqual(metrics["realized_tax"], Decimal("100.00"))



    def test_split_additional_quantity_preserves_fifo_cost_basis_and_acquisition_date(self):
        common = {
            "owner": self.user,
            "family": self.family,
            "family_name": "Member Split",
            "portfolio": "Direct Equity",
            "asset_class": "Equity",
            "sub_class": "Direct Equity",
            "asset_name": "MIS Equity",
            "asset": self.asset,
        }
        buy = Transaction.objects.create(
            **common,
            transaction_date=date(2025, 1, 1),
            transaction_type="BUY",
            quantity=Decimal("10"),
            price_per_unit=Decimal("100"),
            amount=Decimal("1000"),
            fees=Decimal("0"),
        )
        split = Transaction.objects.create(
            **common,
            transaction_date=date(2026, 1, 1),
            transaction_type="SPLIT",
            quantity=Decimal("10"),
            price_per_unit=Decimal("0"),
            amount=Decimal("0"),
            fees=Decimal("0"),
        )
        sell = Transaction.objects.create(
            **common,
            transaction_date=date(2026, 2, 1),
            transaction_type="SELL",
            quantity=Decimal("5"),
            price_per_unit=Decimal("60"),
            amount=Decimal("300"),
            fees=Decimal("0"),
        )

        metrics = MISReportService._fifo_tax_metrics(
            [buy, split, sell],
            "asset",
            date(2026, 2, 28),
            date(2026, 1, 1),
            {self.asset.id: None},
            {self.asset.id: Decimal("60")},
        )

        # The 10 additional shares double the original lot quantity and halve
        # unit cost; they do not become a zero-cost lot with a new acquisition date.
        self.assertEqual(metrics["realized_pnl"], Decimal("50"))
        self.assertEqual(metrics["unrealized_pnl"], Decimal("150"))
        position_quantity, invested = MISReportService._position_at(
            [buy, split, sell],
            date(2026, 2, 28),
        )
        self.assertEqual(position_quantity, Decimal("15"))
        self.assertEqual(invested, Decimal("750"))

    def test_fifo_tax_metrics_include_buy_and_sell_fees(self):
        common = {
            "owner": self.user,
            "family": self.family,
            "family_name": "Member Fees",
            "portfolio": "Direct Equity",
            "asset_class": "Equity",
            "sub_class": "Direct Equity",
            "asset_name": "MIS Equity",
            "asset": self.asset,
        }
        buy = Transaction.objects.create(
            **common,
            transaction_date=date(2026, 1, 1),
            transaction_type="BUY",
            quantity=Decimal("10"),
            price_per_unit=Decimal("100"),
            amount=Decimal("1000"),
            fees=Decimal("10"),
        )
        sell = Transaction.objects.create(
            **common,
            transaction_date=date(2026, 6, 1),
            transaction_type="SELL",
            quantity=Decimal("5"),
            price_per_unit=Decimal("140"),
            amount=Decimal("700"),
            fees=Decimal("20"),
        )
        setting = TaxRateSetting.objects.create(
            family=self.family,
            asset=self.asset,
            tenure_months=12,
            short_term_tax_rate=Decimal("20"),
            long_term_tax_rate=Decimal("10"),
        )

        metrics = MISReportService._fifo_tax_metrics(
            [buy, sell],
            "asset",
            date(2026, 6, 30),
            date(2026, 1, 1),
            {self.asset.id: setting},
            {self.asset.id: Decimal("140")},
        )

        # Buy fees raise unit cost to ₹101; sell fees reduce net proceeds
        # to ₹680. Realized gain is ₹175 and the configured 20% estimate is ₹35.
        self.assertEqual(metrics["realized_pnl"], Decimal("175"))
        self.assertEqual(metrics["realized_tax"], Decimal("35.00"))
        # Five remaining units retain the fee-inclusive ₹101 cost basis.
        self.assertEqual(metrics["unrealized_pnl"], Decimal("195"))

    def test_fifo_holding_months_use_calendar_month_end_anniversaries(self):
        # Jan 31 to the last day of February is one completed month, including
        # leap years. This classification is shared by tax tenure thresholds.
        self.assertEqual(
            MISReportService._holding_months(date(2025, 1, 31), date(2025, 2, 28)),
            1,
        )
        self.assertEqual(
            MISReportService._holding_months(date(2024, 1, 31), date(2024, 2, 29)),
            1,
        )
        self.assertEqual(
            MISReportService._holding_months(date(2025, 1, 31), date(2025, 3, 30)),
            1,
        )
        self.assertEqual(
            MISReportService._holding_months(date(2025, 1, 31), date(2025, 3, 31)),
            2,
        )

        setting = TaxRateSetting(
            tenure_months=1,
            short_term_tax_rate=Decimal("15"),
            long_term_tax_rate=Decimal("10"),
        )
        self.assertEqual(
            MISReportService._tax_rate_for_lot(
                {"acquired_on": date(2025, 1, 31)},
                setting,
                date(2025, 2, 28),
            ),
            Decimal("0.10"),
        )
