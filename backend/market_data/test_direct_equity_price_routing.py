from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase

from investments.models import Asset, AssetCategory, Transaction
from market_data.models import DataSource, MarketPrice
from market_data.services.market_data_manager import MarketDataManager
from market_data.services.market_price_scope import (
    is_legacy_direct_equity_asset,
    market_price_assets,
)
from users.models import FamilyGroup


class LegacyDirectEquityPriceRoutingTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="legacy_equity_price_owner",
            password="test-password",
        )
        self.family = FamilyGroup.objects.create(name="Legacy Equity Price Family")
        self.asset = Asset.objects.create(
            owner=self.user,
            family=self.family,
            name="ITC Limited",
            category=AssetCategory.OTHER,
            isin="INE154A01025",
            currency="INR",
            is_active=True,
        )
        Transaction.objects.create(
            owner=self.user,
            family=self.family,
            family_name="Member A",
            portfolio="Direct Equity",
            asset_class="Equity",
            sub_class="Direct Equity",
            asset_name="Direct Equity",
            underlying="ITC Limited",
            advisors="",
            asset=self.asset,
            transaction_type="BUY",
            transaction_date=date(2026, 1, 5),
            quantity=Decimal("10"),
            price_per_unit=Decimal("200"),
            amount=Decimal("2000"),
            fees=Decimal("0"),
        )

    def test_legacy_direct_equity_is_in_both_refresh_universes(self):
        self.assertTrue(is_legacy_direct_equity_asset(self.asset))

        command_asset_ids = set(
            market_price_assets(include_mutual_funds=False).values_list("id", flat=True)
        )
        scheduler_asset_ids = set(
            market_price_assets(include_mutual_funds=True).values_list("id", flat=True)
        )

        self.assertIn(self.asset.id, command_asset_ids)
        self.assertIn(self.asset.id, scheduler_asset_ids)

    def test_unrelated_other_asset_remains_out_of_refresh_universe(self):
        unrelated = Asset.objects.create(
            owner=self.user,
            family=self.family,
            name="Unlisted Private Investment",
            category=AssetCategory.OTHER,
            currency="INR",
            is_active=True,
        )

        self.assertFalse(is_legacy_direct_equity_asset(unrelated))
        self.assertNotIn(
            unrelated.id,
            set(market_price_assets(include_mutual_funds=False).values_list("id", flat=True)),
        )

    def test_legacy_direct_equity_asset_fetches_and_persists_yahoo_price(self):
        holding = SimpleNamespace(
            id=101,
            current_price=Decimal("456.70"),
            current_value=Decimal("4567.00"),
        )

        def save_history(*, asset, symbol, **kwargs):
            self.assertEqual(symbol, "ITC.NS")
            MarketPrice.objects.create(
                asset=asset,
                date=date.today(),
                source=DataSource.YAHOO_FINANCE,
                close_price=Decimal("456.70"),
                adjusted_close=Decimal("456.70"),
            )
            return 1

        with (
            patch.object(
                MarketDataManager,
                "resolve_asset_symbol",
                return_value="ITC.NS",
            ),
            patch(
                "market_data.services.market_data_manager.YahooFinanceService.save_history",
                side_effect=save_history,
            ) as fetch_history,
            patch.object(
                MarketDataManager,
                "_rebuild_holding",
                return_value=holding,
            ),
        ):
            result = MarketDataManager.fetch_and_rebuild(self.asset)

        self.assertTrue(result["success"])
        self.assertFalse(result["skipped"])
        self.assertEqual(result["symbol"], "ITC.NS")
        self.assertEqual(result["records"], 1)
        fetch_history.assert_called_once()
        stored_price = MarketPrice.objects.get(
            asset=self.asset,
            source=DataSource.YAHOO_FINANCE,
        )
        self.assertEqual(stored_price.close_price, Decimal("456.70"))

    def test_unrelated_other_asset_is_not_sent_to_yahoo(self):
        unrelated = Asset.objects.create(
            owner=self.user,
            family=self.family,
            name="Unlisted Private Investment",
            category=AssetCategory.OTHER,
            currency="INR",
            is_active=True,
        )

        with patch.object(MarketDataManager, "resolve_asset_symbol") as resolve_symbol:
            result = MarketDataManager.fetch_and_rebuild(unrelated)

        self.assertFalse(result["success"])
        self.assertTrue(result["skipped"])
        resolve_symbol.assert_not_called()

    def test_resolved_symbol_is_persisted_without_overwriting_existing_symbol(self):
        from market_data.services.security_resolver import SecurityResolver

        with patch.object(
            SecurityResolver,
            "resolve_yahoo_symbol",
            return_value="ITCHOTELS.NS",
        ):
            resolved = MarketDataManager.resolve_asset_symbol(self.asset)

        self.assertEqual(resolved, "ITCHOTELS.NS")
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.symbol, "ITCHOTELS.NS")

        # An explicit non-empty symbol is never overwritten by later mapping.
        self.asset.symbol = "CUSTOM.NS"
        self.asset.save(update_fields=["symbol"])
        with patch.object(
            SecurityResolver,
            "resolve_yahoo_symbol",
            return_value="DIFFERENT.NS",
        ):
            resolved = MarketDataManager.resolve_asset_symbol(self.asset)
        self.assertEqual(resolved, "DIFFERENT.NS")
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.symbol, "CUSTOM.NS")

    def test_resolver_persists_only_when_asset_symbol_is_blank(self):
        from market_data.services.security_resolver import SecurityResolver

        with patch.object(
            SecurityResolver,
            "resolve_yahoo_symbol",
            return_value="ITCHOTELS.NS",
        ):
            resolved = MarketDataManager.resolve_asset_symbol(self.asset)

        self.assertEqual(resolved, "ITCHOTELS.NS")
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.symbol, "ITCHOTELS.NS")

        self.asset.symbol = "EXPLICIT.NS"
        self.asset.save(update_fields=["symbol"])
        with patch.object(
            SecurityResolver,
            "resolve_yahoo_symbol",
            return_value="OTHER.NS",
        ):
            resolved = MarketDataManager.resolve_asset_symbol(self.asset)

        self.assertEqual(resolved, "OTHER.NS")
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.symbol, "EXPLICIT.NS")

    def test_decorated_entrypoint_reports_lock_contention(self):
        from config.postgres_advisory_lock import with_postgres_advisory_lock

        @with_postgres_advisory_lock("test_refresh")
        def run_refresh():
            return {"success": True}

        with patch(
            "config.postgres_advisory_lock.postgres_advisory_lock"
        ) as lock_context:
            lock_context.return_value.__enter__.return_value = False
            lock_context.return_value.__exit__.return_value = False
            result = run_refresh()

        self.assertFalse(result["success"])
        self.assertTrue(result["skipped"])
        self.assertFalse(result["lock_acquired"])
        self.assertIn("held by another process", result["reason"])
