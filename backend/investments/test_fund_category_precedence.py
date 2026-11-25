"""Sub-class "Mutual Fund" wins over asset class Debt/Equity for Asset.category."""
from datetime import date
from decimal import Decimal
from io import StringIO

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import SimpleTestCase, TestCase

from investments.models import Asset, AssetCategory, Transaction
from investments.services.fund_category_repair import apply_fund_category_repair, find_mislabelled_funds
from investments.services.transaction_import import TransactionImporter
from users.models import FamilyGroup


class CategoryPrecedenceTests(SimpleTestCase):
    def test_mutual_fund_sub_class_wins_over_debt_and_equity(self):
        category = TransactionImporter._internal_category
        self.assertEqual(category("Debt", "Mutual Fund"), AssetCategory.MUTUAL_FUND)
        self.assertEqual(category("Equity", "Mutual Fund"), AssetCategory.MUTUAL_FUND)
        self.assertEqual(category("Bond", "Mutual Fund"), AssetCategory.MUTUAL_FUND)
        self.assertEqual(category("Equity", "Equity Mutual Fund"), AssetCategory.MUTUAL_FUND)
        self.assertEqual(category("Hybrid", "Mutual Fund"), AssetCategory.MUTUAL_FUND)

    def test_other_combinations_are_unchanged(self):
        category = TransactionImporter._internal_category
        self.assertEqual(category("Equity", "Direct Equity"), AssetCategory.STOCK)
        self.assertEqual(category("Debt", "Bonds"), AssetCategory.BOND)
        self.assertEqual(category("Cash", "Savings"), AssetCategory.CASH)
        # Explicit technical asset classes keep their priority.
        self.assertEqual(category("ETF", "Mutual Fund"), AssetCategory.ETF)
        self.assertEqual(category("Gold", "Mutual Fund"), AssetCategory.GOLD)
        self.assertEqual(category("Mutual_Fund", ""), AssetCategory.MUTUAL_FUND)
        self.assertEqual(category("", ""), AssetCategory.OTHER)


class FundImportTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="fund_importer", password="pw")
        self.family = FamilyGroup.objects.create(name="Fund Family")

    def test_new_debt_fund_is_created_as_mutual_fund(self):
        asset = TransactionImporter._get_or_create_asset(
            owner=self.user, family=self.family, asset_name="BANDHAN Dynamic Bond Fund-Direct Plan-Growth",
            isin="INF194K01Y29", asset_class="Debt", sub_class="Mutual Fund",
        )
        self.assertEqual(asset.category, AssetCategory.MUTUAL_FUND)

    def test_reimport_reuses_a_legacy_bond_asset_instead_of_duplicating_it(self):
        legacy = Asset.objects.create(
            owner=self.user, family=self.family, name="BANDHAN Dynamic Bond Fund-Direct Plan-Growth",
            category=AssetCategory.BOND, isin="INF194K01Y29", currency="INR", is_active=True,
        )
        asset = TransactionImporter._get_or_create_asset(
            owner=self.user, family=self.family, asset_name="BANDHAN Dynamic Bond Fund-Direct Plan-Growth",
            isin="INF194K01Y29", asset_class="Debt", sub_class="Mutual Fund",
        )
        self.assertEqual(asset.id, legacy.id)
        self.assertEqual(Asset.objects.filter(isin="INF194K01Y29").count(), 1)
        legacy.refresh_from_db()
        self.assertEqual(legacy.category, AssetCategory.MUTUAL_FUND)


class FundCategoryRepairTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="fund_repair", password="pw")
        self.family = FamilyGroup.objects.create(name="Repair Family")

    def _asset(self, name, category, isin, asset_class, sub_class):
        asset = Asset.objects.create(
            owner=self.user, family=self.family, name=name, category=category, isin=isin,
            currency="INR", is_active=True,
        )
        Transaction.objects.create(
            owner=self.user, family=self.family, family_name="M", portfolio="P", asset_class=asset_class,
            sub_class=sub_class, asset_name=name, underlying="", asset=asset, transaction_type="BUY",
            transaction_date=date(2026, 1, 5), quantity=Decimal("1"), price_per_unit=Decimal("10"),
            amount=Decimal("10"), fees=Decimal("0"),
        )
        return asset

    def test_lists_only_mislabelled_funds(self):
        debt_fund = self._asset("Debt Fund Growth", AssetCategory.BOND, "INF000D01011", "Debt", "Mutual Fund")
        equity_fund = self._asset("Equity Fund Growth", AssetCategory.STOCK, "INF000E01011", "Equity", "Mutual Fund")
        self._asset("Real Bond", AssetCategory.BOND, "INE000B07011", "Debt", "Bonds")
        self._asset("Real Stock", AssetCategory.STOCK, "INE000S01011", "Equity", "Direct Equity")
        self._asset("Gold BeES", AssetCategory.STOCK, "INF204KB17I5", "Equity", "Mutual Fund")  # ETF by name
        self._asset("Correct Fund", AssetCategory.MUTUAL_FUND, "INF000C01011", "Equity", "Mutual Fund")

        changes = find_mislabelled_funds(Asset, Transaction)
        self.assertEqual(
            {(item["asset_id"], item["old_category"]) for item in changes["change"]},
            {(debt_fund.id, AssetCategory.BOND), (equity_fund.id, AssetCategory.STOCK)},
        )

    def test_collision_with_existing_mutual_fund_asset_is_reported_not_changed(self):
        self._asset("Dup Fund", AssetCategory.MUTUAL_FUND, "INF000X01011", "Debt", "Mutual Fund")
        legacy = self._asset("Dup Fund", AssetCategory.BOND, "INF000X01011", "Debt", "Mutual Fund")
        changes = find_mislabelled_funds(Asset, Transaction)
        self.assertEqual(changes["change"], [])
        self.assertEqual([item["asset_id"] for item in changes["conflicts"]], [legacy.id])
        apply_fund_category_repair(Asset, Transaction)
        legacy.refresh_from_db()
        self.assertEqual(legacy.category, AssetCategory.BOND)

    def test_apply_changes_only_the_category(self):
        fund = self._asset("Debt Fund Growth", AssetCategory.BOND, "INF000D01011", "Debt", "Mutual Fund")
        before = Asset.objects.filter(pk=fund.pk).values("name", "isin", "symbol", "family_id", "owner_id").get()
        changed = apply_fund_category_repair(Asset, Transaction)
        fund.refresh_from_db()
        self.assertEqual(changed, 1)
        self.assertEqual(fund.category, AssetCategory.MUTUAL_FUND)
        self.assertEqual(Asset.objects.filter(pk=fund.pk).values("name", "isin", "symbol", "family_id", "owner_id").get(), before)
        self.assertEqual(Transaction.objects.filter(asset=fund).count(), 1)

    def test_already_applied_lists_funds_0111_most_likely_changed(self):
        from datetime import timedelta

        from django.db.migrations.recorder import MigrationRecorder

        applied = MigrationRecorder.Migration.objects.get(
            app="investments", name="0111_repair_mutual_fund_categories"
        ).applied
        repaired = self._asset("Debt Fund Growth", AssetCategory.MUTUAL_FUND, "INF000D01011", "Debt", "Mutual Fund")
        always_fund = self._asset("Plain Fund", AssetCategory.MUTUAL_FUND, "INF000P01011", "Mutual Fund", "Mutual Fund")
        self._asset("Later Fund", AssetCategory.MUTUAL_FUND, "INF000L01011", "Equity", "Mutual Fund")
        Asset.objects.filter(pk__in=[repaired.pk, always_fund.pk]).update(created_at=applied - timedelta(days=30))

        out = StringIO()
        call_command("report_fund_categories", "--already-applied", stdout=out)
        report = out.getvalue()
        self.assertIn("Debt Fund Growth", report)
        self.assertIn("BOND", report)
        from config.scheduling.clock import localize

        self.assertIn(f"{localize(applied):%Y-%m-%d %H:%M %Z}", report)  # shown in IST, not UTC
        self.assertNotIn("Plain Fund", report)   # the old importer made it MUTUAL_FUND too
        self.assertNotIn("Later Fund", report)   # created after 0111 ran
        repaired.refresh_from_db()
        self.assertEqual(repaired.category, AssetCategory.MUTUAL_FUND)

    def test_csv_locked_by_excel_is_reported_not_raised(self):
        from unittest.mock import patch

        self._asset("Debt Fund Growth", AssetCategory.BOND, "INF000D01011", "Debt", "Mutual Fund")
        for extra in ([], ["--already-applied"]):
            out, err = StringIO(), StringIO()
            with patch(
                "investments.management.commands.report_fund_categories.atomic_write_text",
                side_effect=PermissionError(13, "The process cannot access the file"),
            ) as write:
                call_command("report_fund_categories", "--csv", *extra, stdout=out, stderr=err)
            write.assert_called_once()
            self.assertIn("Could not write", err.getvalue())
            self.assertIn("close it in Excel", err.getvalue())

    def test_csv_is_written_into_a_new_reports_folder(self):
        import csv
        import tempfile
        from pathlib import Path

        from django.test import override_settings

        self._asset("Debt Fund Growth", AssetCategory.BOND, "INF000D01011", "Debt", "Mutual Fund")
        with tempfile.TemporaryDirectory() as tmp, override_settings(PWMS_DATA_DIR=tmp):
            call_command("report_fund_categories", "--csv", stdout=StringIO())
            with (Path(tmp) / "reports" / "fund_category_repair.csv").open(encoding="utf-8", newline="") as handle:
                rows = list(csv.DictReader(handle))
        self.assertEqual([(row["action"], row["isin"]) for row in rows], [("change", "INF000D01011")])

    def test_report_command_is_read_only(self):
        fund = self._asset("Debt Fund Growth", AssetCategory.BOND, "INF000D01011", "Debt", "Mutual Fund")
        out = StringIO()
        call_command("report_fund_categories", stdout=out)
        fund.refresh_from_db()
        self.assertEqual(fund.category, AssetCategory.BOND)
        self.assertIn("Debt Fund Growth", out.getvalue())
        self.assertIn("INF000D01011", out.getvalue())
