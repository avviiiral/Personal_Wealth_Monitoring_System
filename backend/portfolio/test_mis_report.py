from decimal import Decimal
from datetime import date
from io import BytesIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from openpyxl import load_workbook
from rest_framework.test import APIClient

from investments.models import Asset, Transaction
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
        self.assertEqual(data["tax_report"][0]["realized_tax"], 0.0)
        self.assertEqual(data["tax_report"][0]["unrealized_tax"], 0.0)
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
        self.assertEqual(row["realized_tax"], 275.0)

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

    def test_tax_setting_applies_to_same_display_asset_name_across_positions(self):
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
            family=self.family,
            asset=first_asset,
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
        self.assertEqual(
            {item["unrealized_tax"] for item in rows},
            {150.0},
        )

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
        tax_ws = workbook["Tax Report"]
        self.assertEqual(tax_ws["A1"].value, "Tax Report")
        self.assertEqual(tax_ws["Q3"].value, "Taxation")
        self.assertEqual(tax_ws["Q4"].value, "Realized P/L")
        self.assertEqual(tax_ws["R4"].value, "Unrealized P/L")
        self.assertEqual(tax_ws["S4"].value, "Realized Tax")
        self.assertEqual(tax_ws["T4"].value, "Unrealized Tax")
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
        MarketPrice.objects.create(
            asset=gold_etf,
            date=date.today(),
            close_price=Decimal("131.25"),
            source=DataSource.MANUAL,
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
