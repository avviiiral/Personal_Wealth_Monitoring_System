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
            family_name="Legacy Family Label",
            portfolio="Core",
            asset_class="Equity",
            sub_class="Large Cap",
            asset_name="MIS Equity",
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

    def test_report_uses_active_family_scope_and_existing_values(self):
        response = self.client.get("/api/portfolio/mis-report/")

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["family_name"], "MIS Test Family")
        self.assertEqual(data["summary"]["total_invested"], 1000.0)
        self.assertEqual(data["summary"]["total_current_value"], 1250.0)
        self.assertEqual(data["summary"]["total_pnl"], 250.0)
        self.assertEqual(data["holdings"][0]["asset_class"], "Equity")
        self.assertEqual(data["holdings"][0]["asset_name"], "MIS Equity")

    def test_mutual_fund_holdings_are_included(self):
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
            family_name="MIS Test Family",
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

        response = self.client.get("/api/portfolio/mis-report/")
        self.assertEqual(response.status_code, 200)

        mf_rows = [
            row for row in response.json()["holdings"]
            if row["asset_class"] == "Mutual Funds"
        ]
        self.assertEqual(len(mf_rows), 1)
        self.assertEqual(mf_rows[0]["current_value"], 1200.0)

    def test_unrelated_family_is_not_accessible(self):
        other_family = FamilyGroup.objects.create(name="Other Family")
        self.user.profile.role = Role.VIEWER

        # Simulate a stale/forged active-family selection. The user is
        # deliberately NOT a member of the selected family.
        self.user.profile.family_groups.remove(self.family)
        self.user.profile.active_family_group = other_family
        self.user.profile.save(update_fields=["role", "active_family_group"])

        response = self.client.get("/api/portfolio/mis-report/")
        self.assertEqual(response.status_code, 403)

    def test_empty_family_returns_empty_report(self):
        empty_family = FamilyGroup.objects.create(name="Empty MIS Family")
        self.user.profile.family_groups.add(empty_family)
        self.user.profile.active_family_group = empty_family
        self.user.profile.save(update_fields=["active_family_group"])

        response = self.client.get("/api/portfolio/mis-report/")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["holdings"], [])
        self.assertEqual(data["summary"]["total_invested"], 0.0)
        self.assertEqual(data["summary"]["total_current_value"], 0.0)

    def test_excel_download_is_xlsx_and_contains_expected_sheets(self):
        response = self.client.get("/api/portfolio/mis-report/download/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response["Content-Type"],
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

        workbook = load_workbook(BytesIO(response.content))
        self.assertEqual(workbook.sheetnames, ["MIS Summary", "Holdings"])
        self.assertEqual(workbook["MIS Summary"]["B2"].value, "MIS Test Family")
        self.assertEqual(workbook["Holdings"]["A2"].value, "MIS Test Family")
        self.assertEqual(workbook["Holdings"]["C2"].value, "Equity")
        self.assertEqual(workbook["Holdings"]["E2"].value, "MIS Equity")
