from datetime import date
from decimal import Decimal
import io

import openpyxl
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from unittest.mock import patch

from users.models import FamilyGroup

from .models import (
    Asset,
    AssetCategory,
    SecurityMaster,
    Transaction,
    TransactionSource,
    TransactionType,
)
from .services.security_master import SecurityMasterService
from .services.transaction_import import TransactionImporter


class SecurityMasterServiceTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="security_test",
            password="test-password",
        )
        self.family = FamilyGroup.objects.create(name="Security Family")
        self.asset = Asset.objects.create(
            owner=self.user,
            family_group=self.family,
            name="Reliance Industries",
            category=AssetCategory.STOCK,
            symbol="RELIANCE",
            isin="INE002A01018",
        )

    def _service_kwargs(self, owner=None, asset=None):
        return {
            "owner": owner or self.user,
            "asset": asset or self.asset,
            "family_group_id": self.family.id,
        }

    def test_get_or_create_creates_security_master(self):
        security = SecurityMasterService.get_or_create(**self._service_kwargs())
        self.assertIsNotNone(security)
        self.assertEqual(security.isin, "INE002A01018")
        self.assertEqual(security.asset_name, "Reliance Industries")
        self.assertEqual(security.family_group_id, self.family.id)

    def test_get_or_create_reuses_existing_isin(self):
        first = SecurityMasterService.get_or_create(**self._service_kwargs())
        second = SecurityMasterService.get_or_create(**self._service_kwargs())
        self.assertEqual(first.id, second.id)
        self.assertEqual(
            SecurityMaster.objects.filter(
                family_group=self.family,
                isin="INE002A01018",
            ).count(),
            1,
        )

    def test_get_for_asset_returns_security(self):
        created = SecurityMasterService.get_or_create(**self._service_kwargs())
        result = SecurityMasterService.get_for_asset(**self._service_kwargs())
        self.assertIsNotNone(result)
        self.assertEqual(result.id, created.id)

    def test_classification_update(self):
        security = SecurityMasterService.get_or_create(**self._service_kwargs())
        updated = SecurityMasterService.update_classification(
            owner=self.user,
            security_id=security.id,
            sector="Financial Services",
            cap_type="Large Cap",
            family_group_id=self.family.id,
        )
        self.assertEqual(updated.sector, "Financial Services")
        self.assertEqual(updated.cap_type, "Large Cap")

    def test_security_master_is_family_specific(self):
        other_user = User.objects.create_user(
            username="other_security_test",
            password="test-password",
        )
        other_family = FamilyGroup.objects.create(name="Other Security Family")
        other_asset = Asset.objects.create(
            owner=other_user,
            family_group=other_family,
            name="Reliance Industries",
            category=AssetCategory.STOCK,
            symbol="RELIANCE",
            isin="INE002A01018",
        )

        first = SecurityMasterService.get_or_create(**self._service_kwargs())
        second = SecurityMasterService.get_or_create(
            owner=other_user,
            asset=other_asset,
            family_group_id=other_family.id,
        )

        self.assertNotEqual(first.family_group_id, second.family_group_id)
        self.assertEqual(first.isin, second.isin)

    def test_transaction_can_store_excel_hierarchy(self):
        transaction = Transaction.objects.create(
            owner=self.user,
            family_group=self.family,
            family_name="Family A",
            portfolio="Direct Equity",
            asset_class="Equity",
            sub_class="Large Cap",
            asset_name="Reliance Industries",
            underlying="Reliance Industries Ltd",
            advisors="Advisor A",
            asset=self.asset,
            transaction_type=TransactionType.BUY,
            transaction_date=date(2026, 1, 1),
            quantity=Decimal("10"),
            price_per_unit=Decimal("1000"),
            amount=Decimal("10000"),
            fees=Decimal("10"),
            source=TransactionSource.EXCEL,
            source_key="security-test-001",
        )
        self.assertEqual(transaction.family_group_id, self.family.id)
        self.assertEqual(transaction.family_name, "Family A")
        self.assertEqual(transaction.portfolio, "Direct Equity")
        self.assertEqual(transaction.asset_class, "Equity")
        self.assertEqual(transaction.sub_class, "Large Cap")
        self.assertEqual(transaction.asset_name, "Reliance Industries")
        self.assertEqual(transaction.underlying, "Reliance Industries Ltd")
        self.assertEqual(transaction.advisors, "Advisor A")
        self.assertEqual(transaction.source, TransactionSource.EXCEL)

    def test_get_or_create_derives_amc_name_for_mutual_fund(self):
        fund_asset = Asset.objects.create(
            owner=self.user,
            family_group=self.family,
            name="Kotak Liquid - Growth - Direct",
            category=AssetCategory.MUTUAL_FUND,
            isin="INF174K01NE8",
        )
        security = SecurityMasterService.get_or_create(**self._service_kwargs(asset=fund_asset))
        self.assertEqual(security.amc_name, "Kotak Mahindra Mutual Fund")

    def test_get_or_create_does_not_derive_amc_name_for_stock(self):
        security = SecurityMasterService.get_or_create(**self._service_kwargs())
        self.assertIsNone(security.amc_name)

    def test_get_or_create_never_overwrites_existing_amc_name(self):
        fund_asset = Asset.objects.create(
            owner=self.user,
            family_group=self.family,
            name="Kotak Liquid - Growth - Direct",
            category=AssetCategory.MUTUAL_FUND,
            isin="INF174K01NE8",
        )
        SecurityMaster.objects.create(
            owner=self.user,
            family_group=self.family,
            isin="INF174K01NE8",
            asset_name="Kotak Liquid - Growth - Direct",
            amc_name="Manually Corrected AMC Name",
        )
        security = SecurityMasterService.get_or_create(**self._service_kwargs(asset=fund_asset))
        self.assertEqual(security.amc_name, "Manually Corrected AMC Name")

    def test_get_or_create_leaves_amc_name_null_for_unknown_fund_house(self):
        fund_asset = Asset.objects.create(
            owner=self.user,
            family_group=self.family,
            name="Totally Unknown Fund House Scheme - Growth",
            category=AssetCategory.MUTUAL_FUND,
            isin="INFUNKNOWN0001",
        )
        security = SecurityMasterService.get_or_create(**self._service_kwargs(asset=fund_asset))
        self.assertIsNone(security.amc_name)


def _build_transactions_workbook(include_summary):
    workbook = openpyxl.Workbook()
    transactions_sheet = workbook.active
    transactions_sheet.title = "Transactions"
    transactions_sheet.append([
        "Family Name", "Asset Class", "Sub Class", "Asset Name",
        "Underlying", "Advisors", "ISIN", "Date", "Trans. Type",
        "Quantity", "Price", "Amount",
    ])
    transactions_sheet.append([
        "Test Family", "Equity", "Direct Equity", "Reliance Industries",
        "Reliance Industries Ltd", "DIRECT", "INE002A01018", "2026-07-16",
        "Buy", "100", "1897.87", "189787.00",
    ])

    if include_summary:
        summary_sheet = workbook.create_sheet("Summary")
        summary_sheet.append([None] * 6)
        summary_sheet.append([
            "Family Name", "Portfolio Name", "Asset Class", "Advisors",
            "Asset Name", "ISIN",
        ])
        summary_sheet.append([
            "Test Family", "Direct Equity", "Equity", "DIRECT",
            "Reliance Industries", "INE002A01018",
        ])

    buffer = io.BytesIO()
    workbook.save(buffer)
    buffer.seek(0)
    return SimpleUploadedFile(
        "transactions.xlsx",
        buffer.read(),
        content_type=(
            "application/vnd.openxmlformats-officedocument"
            ".spreadsheetml.sheet"
        ),
    )


class TransactionImportExcelShapeTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="import_shape_test",
            password="test-password",
        )
        self.family = FamilyGroup.objects.create(name="Test Family")

    def _import(self, upload):
        return TransactionImporter.import_file(
            file=upload,
            owner=self.user,
            family_group_id=self.family.id,
        )

    def test_import_succeeds_without_summary_sheet(self):
        result = self._import(_build_transactions_workbook(False))
        self.assertEqual(result["imported_investments"], 1)
        self.assertTrue(
            Asset.objects.filter(
                owner=self.user,
                family_group=self.family,
                isin="INE002A01018",
            ).exists()
        )

    def test_import_succeeds_with_summary_sheet(self):
        result = self._import(_build_transactions_workbook(True))
        self.assertEqual(result["imported_investments"], 1)

    def test_import_returns_touched_asset_ids(self):
        result = self._import(_build_transactions_workbook(False))
        asset = Asset.objects.get(
            owner=self.user,
            family_group=self.family,
            isin="INE002A01018",
        )
        self.assertIn(asset.id, result["touched_asset_ids"])


class AutoPriceRefreshTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="refresh_test",
            password="test-password",
        )
        self.family = FamilyGroup.objects.create(name="Refresh Family")
        self.asset = Asset.objects.create(
            owner=self.user,
            family_group=self.family,
            name="Refresh Target",
            category=AssetCategory.STOCK,
            isin="INE000REFRESH1",
        )

    def test_refresh_assets_calls_fetch_and_rebuild_for_each_asset(self):
        from .services.auto_price_refresh import _refresh_assets

        with patch(
            "market_data.services.market_data_manager.MarketDataManager.fetch_and_rebuild"
        ) as mock_fetch:
            mock_fetch.return_value = {"success": True}
            _refresh_assets([self.asset.id])

        mock_fetch.assert_called_once()
        self.assertEqual(mock_fetch.call_args[0][0].id, self.asset.id)

    def test_refresh_assets_does_not_raise_on_individual_failure(self):
        from .services.auto_price_refresh import _refresh_assets

        with patch(
            "market_data.services.market_data_manager.MarketDataManager.fetch_and_rebuild"
        ) as mock_fetch:
            mock_fetch.side_effect = Exception("boom")
            with self.assertLogs(
                "investments.services.auto_price_refresh",
                level="ERROR",
            ):
                _refresh_assets([self.asset.id])

    def test_refresh_assets_async_ignores_empty_input(self):
        from .services.auto_price_refresh import refresh_assets_async

        with patch("threading.Thread") as mock_thread:
            refresh_assets_async([])

        mock_thread.assert_not_called()
