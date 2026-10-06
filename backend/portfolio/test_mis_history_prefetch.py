from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from investments.models import Asset, Transaction
from market_data.models import ManualAssetPrice
from mutual_funds.models import (
    AMFIMasterNAV,
    AMFIMasterScheme,
    MutualFundScheme,
    MutualFundTransaction,
    MutualFundTransactionType,
)
from portfolio.mis_history_prefetch import MISHistoryPrefetch
from portfolio.mis_report_service import MISReportService
from users.models import FamilyGroup


class MISHistoryTestBase(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="mis_history_user", password="test-password"
        )
        self.family = FamilyGroup.objects.create(name="History Family")
        self.user.profile.family_groups.add(self.family)
        self.user.profile.active_family_group = self.family
        self.user.profile.save(update_fields=["active_family_group"])
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

        for target in ("_reference_rate",):
            patcher = patch.object(
                MISReportService, target, return_value=(None, None)
            )
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch.object(MISReportService, "_bse500_rate", return_value=None)
        patcher.start()
        self.addCleanup(patcher.stop)

    def make_mf(self, code="HIST001", first_tx=date(2026, 1, 15), master=True):
        scheme = MutualFundScheme.objects.create(
            owner=self.user,
            family=self.family,
            scheme_name=f"History Fund {code}",
            scheme_code=code,
            category="Equity",
            isin_growth=f"INF{code}",
        )
        MutualFundTransaction.objects.create(
            owner=self.user,
            family=self.family,
            family_name="DAJ",
            portfolio="Core",
            scheme=scheme,
            transaction_type=MutualFundTransactionType.PURCHASE,
            transaction_date=first_tx,
            units=Decimal("10"),
            nav=Decimal("100"),
            amount=Decimal("1000"),
            fees=Decimal("0"),
        )
        master_row = None
        if master:
            master_row = AMFIMasterScheme.objects.create(
                scheme_code=code,
                scheme_name=scheme.scheme_name,
                isin_growth=scheme.isin_growth,
            )
        return scheme, master_row


class MISPageIsDatabaseOnlyTests(MISHistoryTestBase):
    def test_report_page_never_downloads_amfi_history(self):
        self.make_mf()
        with patch(
            "portfolio.mis_report_service.AMFIService.import_historical_master_navs"
        ) as importer, patch(
            "mutual_funds.services.amfi.AMFIService.download_historical_nav"
        ) as downloader:
            response = self.client.get("/api/portfolio/mis-report/")
            notes = self.client.get("/api/portfolio/mis-report/notes/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(notes.status_code, 200)
        importer.assert_not_called()
        downloader.assert_not_called()

    def test_excel_download_still_tops_up_missing_history(self):
        self.make_mf()
        with patch(
            "portfolio.mis_report_service.AMFIService.import_historical_master_navs"
        ) as importer, patch.object(
            MISReportService, "refresh_reference_prices", return_value={}
        ):
            response = self.client.get("/api/portfolio/mis-report/download/")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(importer.called)


class MISHistoryPrefetchTests(MISHistoryTestBase):
    def test_imports_full_range_for_scheme_with_no_history(self):
        today = date(2026, 10, 6)
        self.make_mf(first_tx=date(2026, 1, 15))
        with patch(
            "portfolio.mis_history_prefetch.AMFIService.import_historical_master_navs"
        ) as importer:
            summary = MISHistoryPrefetch.run_for_family(self.family, today=today)

        importer.assert_called_once_with(
            date(2026, 1, 8), today, scheme_codes=["HIST001"]
        )
        self.assertEqual(summary, {"schemes": 1, "requests": 1, "failed": 0})

    def test_up_to_date_history_is_not_downloaded_again(self):
        today = date(2026, 10, 6)
        _, master = self.make_mf(first_tx=date(2026, 1, 15))
        for day in (date(2026, 1, 10), date(2026, 10, 5)):
            AMFIMasterNAV.objects.create(scheme=master, date=day, nav=Decimal("10"))
        with patch(
            "portfolio.mis_history_prefetch.AMFIService.import_historical_master_navs"
        ) as importer:
            MISHistoryPrefetch.run_for_family(self.family, today=today)
        importer.assert_not_called()

    def test_only_the_missing_tail_is_downloaded(self):
        today = date(2026, 10, 6)
        _, master = self.make_mf(first_tx=date(2026, 1, 15))
        for day in (date(2026, 1, 10), date(2026, 9, 20)):
            AMFIMasterNAV.objects.create(scheme=master, date=day, nav=Decimal("10"))
        with patch(
            "portfolio.mis_history_prefetch.AMFIService.import_historical_master_navs"
        ) as importer:
            MISHistoryPrefetch.run_for_family(self.family, today=today)
        importer.assert_called_once_with(
            date(2026, 9, 17), today, scheme_codes=["HIST001"]
        )

    def test_closed_scheme_is_not_polled_again(self):
        today = date(2026, 10, 6)
        _, master = self.make_mf(first_tx=date(2024, 1, 15))
        for day in (date(2024, 1, 10), today - timedelta(days=200)):
            AMFIMasterNAV.objects.create(scheme=master, date=day, nav=Decimal("10"))
        with patch(
            "portfolio.mis_history_prefetch.AMFIService.import_historical_master_navs"
        ) as importer:
            MISHistoryPrefetch.run_for_family(self.family, today=today)
        importer.assert_not_called()

    def test_one_unresolvable_scheme_does_not_block_the_others(self):
        today = date(2026, 10, 6)
        self.make_mf(code="GOOD01", first_tx=date(2026, 1, 15))
        self.make_mf(code="BAD001", first_tx=date(2026, 1, 15))

        def fake_import(start, end, scheme_codes=None):
            if "BAD001" in scheme_codes:
                raise RuntimeError("AMFI could not resolve AMC identifiers")

        with patch(
            "portfolio.mis_history_prefetch.AMFIService.import_historical_master_navs",
            side_effect=fake_import,
        ) as importer:
            summary = MISHistoryPrefetch.run_for_family(self.family, today=today)

        attempted = [call.kwargs["scheme_codes"] for call in importer.call_args_list]
        self.assertIn(["GOOD01"], attempted)
        self.assertIn(["BAD001"], attempted)
        self.assertEqual(summary["failed"], 1)

    def test_assets_without_funds_trigger_no_download(self):
        asset = Asset.objects.create(
            owner=self.user, family=self.family, name="Plain Stock",
            category="STOCK", isin="INE000PLAIN01", symbol="PLAIN",
        )
        Transaction.objects.create(
            owner=self.user, family=self.family, asset=asset, family_name="DAJ",
            portfolio="Core", asset_class="Equity", sub_class="Large Cap",
            asset_name="Plain Stock", transaction_date=date(2026, 1, 10),
            transaction_type="BUY", quantity=Decimal("1"),
            price_per_unit=Decimal("10"), amount=Decimal("10"), fees=Decimal("0"),
        )
        with patch(
            "portfolio.mis_history_prefetch.AMFIService.import_historical_master_navs"
        ) as importer:
            summary = MISHistoryPrefetch.run_for_assets([asset.id])
        importer.assert_not_called()
        self.assertEqual(summary[0]["schemes"], 0)

    def test_post_upload_refresh_triggers_history_prefetch(self):
        from investments.services import auto_price_refresh

        asset = Asset.objects.create(
            owner=self.user, family=self.family, name="Uploaded Fund",
            category="MUTUAL_FUND", isin="INF000UPLOAD1",
        )
        with patch.object(auto_price_refresh, "close_old_connections"), patch(
            "market_data.services.market_data_manager.MarketDataManager.fetch_and_rebuild",
            return_value="ok",
        ), patch(
            "investments.services.security_master.SecurityMasterService.get_for_asset",
            return_value=None,
        ), patch(
            "portfolio.mis_history_prefetch.MISHistoryPrefetch.run_for_assets"
        ) as prefetch:
            auto_price_refresh._refresh_assets([asset.id])

        prefetch.assert_called_once_with([asset.id])

    def test_prefetch_failure_does_not_break_post_upload_refresh(self):
        from investments.services import auto_price_refresh

        asset = Asset.objects.create(
            owner=self.user, family=self.family, name="Uploaded Fund 2",
            category="MUTUAL_FUND", isin="INF000UPLOAD2",
        )
        with patch.object(auto_price_refresh, "close_old_connections"), patch(
            "market_data.services.market_data_manager.MarketDataManager.fetch_and_rebuild",
            return_value="ok",
        ), patch(
            "investments.services.security_master.SecurityMasterService.get_for_asset",
            return_value=None,
        ), patch(
            "portfolio.mis_history_prefetch.MISHistoryPrefetch.run_for_assets",
            side_effect=RuntimeError("boom"),
        ):
            auto_price_refresh._refresh_assets([asset.id])  # must not raise


class MISManualPriceTests(MISHistoryTestBase):
    def make_unpriced_asset(self):
        asset = Asset.objects.create(
            owner=self.user, family=self.family, name="Private Holding",
            category="OTHER", isin="INE000PRIVATE1",
        )
        Transaction.objects.create(
            owner=self.user, family=self.family, asset=asset, family_name="DAJ",
            portfolio="Core", asset_class="Alternates", sub_class="Private",
            asset_name="Private Holding", transaction_date=date(2026, 1, 10),
            transaction_type="BUY", quantity=Decimal("10"),
            price_per_unit=Decimal("100"), amount=Decimal("1000"), fees=Decimal("0"),
        )
        return asset

    def row(self, from_date, to_date):
        response = self.client.get(
            f"/api/portfolio/mis-report/?from_date={from_date}&to_date={to_date}"
        )
        self.assertEqual(response.status_code, 200)
        return next(
            item for item in response.json()["data_sheet"]
            if item["asset_name"] == "Private Holding"
        )

    def test_asset_with_no_price_is_carried_at_cost_so_pnl_is_zero(self):
        self.make_unpriced_asset()
        row = self.row("2026-09-30", "2026-10-05")
        self.assertEqual(row["closing_amount"], row["total_cost"])
        self.assertEqual(row["closing_amount"], 1000.0)

    def test_manual_price_applies_from_its_price_date(self):
        asset = self.make_unpriced_asset()
        ManualAssetPrice.objects.create(
            asset=asset, price=Decimal("150"), price_date=date(2026, 10, 1)
        )
        on_or_after = self.row("2026-09-30", "2026-10-05")
        self.assertEqual(on_or_after["closing_nav"], 150.0)
        self.assertEqual(on_or_after["closing_amount"], 1500.0)

        before = self.row("2026-08-01", "2026-09-15")
        self.assertEqual(before["closing_amount"], before["total_cost"])
