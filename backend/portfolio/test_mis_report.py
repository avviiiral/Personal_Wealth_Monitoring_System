from decimal import Decimal
from datetime import date
from io import BytesIO

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
from users.models import FamilyGroup, Role


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
        self.assertEqual(data["data_sheet"][0]["advisor"], "Advisor A")
        self.assertEqual(data["fund_type_summary"], [])

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
        self.assertEqual(data["fund_type_summary"][0]["fund_type"], "Equity")
        self.assertEqual(data["fund_type_summary"][0]["rows"][0]["fund_name"], "MIS Equity Fund")

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
            ["IPS", "Data Sheet", "Fund Type Summary"],
        )
        self.assertEqual(workbook["IPS"]["A1"].value, "Sheet 1 - IPS")
        self.assertEqual(workbook["Data Sheet"]["A1"].value, "Sheet 2 - Data Sheet")
        self.assertEqual(workbook["Data Sheet"]["A4"].value, "Fund Name")
        self.assertEqual(workbook["Data Sheet"]["B6"].value, "DAJ")
        self.assertEqual(workbook["Data Sheet"]["A6"].value, "MIS Equity")
        self.assertEqual(workbook["Fund Type Summary"]["A1"].value, "Sheet 3 - Fund Type wise Summary")
