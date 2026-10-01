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
        self.addCleanup(self.reference_rate_patcher.stop)
        self.addCleanup(self.bse500_rate_patcher.stop)

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
        self.assertEqual(data["notes"]["title"], "Notes to MIS OCTOBER-2026")
        self.assertEqual(data["notes"]["opening_label"], "SEP-26")
        self.assertEqual(data["notes"]["closing_label"], "OCT-26")
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
        self.assertEqual(workbook["Notes"]["A1"].value, "Notes to MIS OCTOBER-2026")
        self.assertEqual(workbook["Data Sheet"]["A1"].value, "Data Sheet")
        self.assertEqual(workbook["Data Sheet"]["E3"].value, "Investment Cost 01.10.2026")
        self.assertEqual(workbook["Data Sheet"]["H3"].value, "SEP-26 Closing MTM")
        self.assertEqual(workbook["Data Sheet"]["K3"].value, "Transactions- Buy/Sell 30.09.2026 to 01.10.2026")
        self.assertEqual(workbook["Data Sheet"]["N3"].value, "OCT-26 Closing MTM")
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
        self.assertEqual(workbook["IPS"]["E4"].value, 0.0125)
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
