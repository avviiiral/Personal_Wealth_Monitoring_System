"""15-minute stock/ETF price job: freshness, scope, ordering, deadlines.

All tests use a fixed clock. 25 Nov 2026 is a Wednesday.
"""
import threading
import time
from datetime import date, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pandas as pd
from django.contrib.auth.models import User
from django.test import SimpleTestCase, TestCase
from rest_framework import serializers

from config.scheduling.clock import MarketCalendar
from investments.models import Asset, AssetCategory, Transaction
from market_data.models import DataSource, MarketPrice
from market_data.services.market_data_manager import MarketDataManager
from market_data.services.market_price_scope import (
    is_mutual_fund_asset,
    market_price_assets,
    price_loop_assets,
)
from market_data.services.price_freshness import PriceFreshness
from market_data.services.yahoo_finance import YahooFinanceService
from users.models import FamilyGroup

IST = ZoneInfo("Asia/Kolkata")


def ist(year, month, day, hour=0, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=IST)


CALENDAR = MarketCalendar(open_time="09:15", close_time="15:30", buffer_minutes=15, holidays=["2026-11-24"])


class PriceFreshnessRuleTests(SimpleTestCase):
    def setUp(self):
        self.freshness = PriceFreshness(CALENDAR)

    def test_no_stored_price_needs_a_fetch(self):
        self.assertTrue(self.freshness.needs_fetch(None, None, ist(2026, 11, 25, 20, 0))[0])

    def test_market_hours_always_fetch_even_if_fetched_minutes_ago(self):
        needed, reason = self.freshness.needs_fetch(date(2026, 11, 25), ist(2026, 11, 25, 10, 31), ist(2026, 11, 25, 10, 42))
        self.assertTrue(needed)
        self.assertIn("market", reason)

    def test_after_close_skip_when_fetched_after_session_end(self):
        needed, _ = self.freshness.needs_fetch(date(2026, 11, 25), ist(2026, 11, 25, 16, 0), ist(2026, 11, 25, 20, 0))
        self.assertFalse(needed)

    def test_after_close_fetch_once_when_last_fetch_was_intraday(self):
        needed, reason = self.freshness.needs_fetch(date(2026, 11, 25), ist(2026, 11, 25, 11, 8), ist(2026, 11, 25, 20, 0))
        self.assertTrue(needed)
        self.assertIn("close", reason)

    def test_rows_without_fetched_at_are_treated_as_stale(self):
        self.assertTrue(self.freshness.needs_fetch(date(2026, 11, 23), None, ist(2026, 11, 25, 20, 0))[0])

    def test_weekend_skips_once_friday_close_is_stored(self):
        self.assertFalse(
            self.freshness.needs_fetch(date(2026, 11, 27), ist(2026, 11, 27, 18, 0), ist(2026, 11, 28, 11, 0))[0]
        )

    def test_holiday_morning_is_not_market_hours(self):
        # 24 Nov is a configured holiday: Monday's close fetched on Monday
        # evening is still current all of Tuesday.
        self.assertFalse(
            self.freshness.needs_fetch(date(2026, 11, 23), ist(2026, 11, 23, 16, 0), ist(2026, 11, 24, 11, 0))[0]
        )

    def test_exact_market_boundaries(self):
        last_fetch = ist(2026, 11, 24, 16, 0)  # irrelevant during market hours
        evening_fetch = ist(2026, 11, 23, 16, 0)
        # 09:14 is before the open: Monday's close fetched Monday evening is current.
        self.assertFalse(self.freshness.needs_fetch(date(2026, 11, 23), evening_fetch, ist(2026, 11, 25, 9, 14))[0])
        # 09:15 is the open: always fetch.
        self.assertTrue(self.freshness.needs_fetch(date(2026, 11, 23), last_fetch, ist(2026, 11, 25, 9, 15))[0])
        # 15:30 is the close but still inside the 15-minute buffer.
        self.assertTrue(self.freshness.needs_fetch(date(2026, 11, 25), ist(2026, 11, 25, 15, 29), ist(2026, 11, 25, 15, 30))[0])
        # 15:44 is the last minute of the buffer.
        self.assertTrue(self.freshness.needs_fetch(date(2026, 11, 25), ist(2026, 11, 25, 15, 40), ist(2026, 11, 25, 15, 44))[0])
        # 15:45: market hours are over; a fetch at 15:40 was intraday, so fetch the close once.
        self.assertTrue(self.freshness.needs_fetch(date(2026, 11, 25), ist(2026, 11, 25, 15, 40), ist(2026, 11, 25, 15, 45))[0])
        # A fetch exactly at 15:45 counts as the final close.
        self.assertFalse(self.freshness.needs_fetch(date(2026, 11, 25), ist(2026, 11, 25, 15, 45), ist(2026, 11, 25, 15, 46))[0])

    def test_before_open_uses_previous_session(self):
        self.assertFalse(
            self.freshness.needs_fetch(date(2026, 11, 25), ist(2026, 11, 25, 16, 0), ist(2026, 11, 26, 8, 0))[0]
        )


class _FakeHistory:
    @staticmethod
    def frame(rows):
        index = pd.DatetimeIndex([pd.Timestamp(day) for day, _ in rows])
        return pd.DataFrame(
            {
                "Open": [price for _, price in rows],
                "High": [price for _, price in rows],
                "Low": [price for _, price in rows],
                "Close": [price for _, price in rows],
                "Adj Close": [price for _, price in rows],
                "Volume": [100 for _ in rows],
            },
            index=index,
        )


class PriceTestBase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="price_owner", password="pw")
        self.family = FamilyGroup.objects.create(name="Price Family")

    def asset(self, name, category, isin=None, symbol=None):
        return Asset.objects.create(
            owner=self.user, family=self.family, name=name, category=category,
            isin=isin, symbol=symbol, currency="INR", is_active=True,
        )

    def transaction(self, asset, *, asset_class="Equity", sub_class="Direct Equity"):
        return Transaction.objects.create(
            owner=self.user, family=self.family, family_name="Member", portfolio="P",
            asset_class=asset_class, sub_class=sub_class, asset_name=asset.name, underlying="",
            asset=asset, transaction_type="BUY", transaction_date=date(2026, 1, 5),
            quantity=Decimal("10"), price_per_unit=Decimal("100"), amount=Decimal("1000"), fees=Decimal("0"),
        )

    def price(self, asset, day, close, *, fetched_at=None, source=DataSource.YAHOO_FINANCE):
        return MarketPrice.objects.create(
            asset=asset, date=day, source=source, close_price=Decimal(close), adjusted_close=Decimal(close),
            fetched_at=fetched_at,
        )


class FetchedAtTests(PriceTestBase):
    def test_save_history_sets_fetched_at_on_insert_and_update(self):
        stock = self.asset("Infosys Limited", AssetCategory.STOCK, "INE009A01021")
        first = ist(2026, 11, 25, 10, 31)
        second = ist(2026, 11, 25, 10, 46)
        with patch.object(YahooFinanceService, "fetch_history", return_value=_FakeHistory.frame([(date(2026, 11, 25), 1500)])):
            with patch("market_data.services.yahoo_finance.timezone.now", return_value=first):
                YahooFinanceService.save_history(asset=stock, symbol="INFY.NS", start=date(2026, 11, 25))
        self.assertEqual(MarketPrice.objects.get(asset=stock).fetched_at, first)

        with patch.object(YahooFinanceService, "fetch_history", return_value=_FakeHistory.frame([(date(2026, 11, 25), 1510)])):
            with patch("market_data.services.yahoo_finance.timezone.now", return_value=second):
                YahooFinanceService.save_history(asset=stock, symbol="INFY.NS", start=date(2026, 11, 25))
        row = MarketPrice.objects.get(asset=stock)
        self.assertEqual(row.fetched_at, second)
        self.assertEqual(row.close_price, Decimal("1510"))

    def test_no_serializer_exposes_fetched_at(self):
        def all_subclasses(cls):
            for subclass in cls.__subclasses__():
                yield subclass
                yield from all_subclasses(subclass)

        import django.apps

        for app in django.apps.apps.get_app_configs():
            for module_name in ("serializers", "underlying_serializers"):
                try:
                    __import__(f"{app.name}.{module_name}")
                except ImportError:
                    continue
        for serializer_class in all_subclasses(serializers.ModelSerializer):
            meta = getattr(serializer_class, "Meta", None)
            if getattr(meta, "model", None) is MarketPrice:
                fields = getattr(meta, "fields", ())
                self.assertNotEqual(fields, "__all__", serializer_class)
                self.assertNotIn("fetched_at", fields, serializer_class)


class IncrementalFetchTests(PriceTestBase):
    """The bug: after one intraday fetch, today's price never moved again."""

    def setUp(self):
        super().setUp()
        self.stock = self.asset("HDFC Bank Limited", AssetCategory.STOCK, "INE040A01034", "HDFCBANK.NS")
        self.transaction(self.stock)
        self.holding = SimpleNamespace(id=1, current_price=Decimal("1"), current_value=Decimal("1"))

    def _fetch(self, now):
        with (
            patch.object(MarketDataManager, "resolve_asset_symbol", return_value="HDFCBANK.NS"),
            patch.object(MarketDataManager, "_rebuild_holding", return_value=self.holding),
            patch.object(MarketDataManager, "_holding_for_current_data", return_value=self.holding),
            patch("market_data.services.market_data_manager.price_now", return_value=now),
            patch.object(YahooFinanceService, "save_history", return_value=1) as save_history,
        ):
            result = MarketDataManager.fetch_and_rebuild(self.stock)
        return result, save_history

    def test_second_intraday_fetch_refetches_today_inclusive(self):
        self.price(self.stock, date(2026, 1, 5), "1500", fetched_at=ist(2026, 1, 5, 16, 0))
        self.price(self.stock, date(2026, 11, 25), "1600", fetched_at=ist(2026, 11, 25, 10, 31))
        result, save_history = self._fetch(ist(2026, 11, 25, 10, 42))
        self.assertFalse(result.get("skipped"), result)
        save_history.assert_called_once()
        self.assertEqual(save_history.call_args.kwargs["start"], date(2026, 11, 25))

    def test_after_close_intraday_row_is_corrected_once(self):
        self.price(self.stock, date(2026, 1, 5), "1500", fetched_at=ist(2026, 1, 5, 16, 0))
        self.price(self.stock, date(2026, 11, 25), "1600", fetched_at=ist(2026, 11, 25, 11, 8))
        result, save_history = self._fetch(ist(2026, 11, 25, 18, 0))
        self.assertFalse(result.get("skipped"), result)
        self.assertEqual(save_history.call_args.kwargs["start"], date(2026, 11, 25))

    def test_after_close_skip_when_final_close_already_stored(self):
        self.price(self.stock, date(2026, 1, 5), "1500", fetched_at=ist(2026, 1, 5, 16, 0))
        self.price(self.stock, date(2026, 11, 25), "1600", fetched_at=ist(2026, 11, 25, 16, 0))
        result, save_history = self._fetch(ist(2026, 11, 25, 18, 0))
        self.assertTrue(result["skipped"])
        save_history.assert_not_called()


class PriceLoopScopeTests(PriceTestBase):
    def test_mutual_funds_are_excluded_from_the_15_minute_loop(self):
        category_fund = self.asset("Some Fund Direct Growth", AssetCategory.MUTUAL_FUND, "INF000A01001")
        debt_fund = self.asset("BANDHAN Dynamic Bond Fund-Direct Plan-Growth", AssetCategory.BOND, "INF194K01Y29")
        self.transaction(debt_fund, asset_class="Debt", sub_class="Mutual Fund")
        inf_only = self.asset("Mislabelled Equity Fund", AssetCategory.STOCK, "INF209K01VF2")
        etf = self.asset("Nippon India ETF Gold BeES", AssetCategory.ETF, "INF204KB17I5")
        bees_other = self.asset("SILVERBEES", AssetCategory.OTHER, "INF109KC1Y56")
        legacy_named_etf = self.asset("Mirae Asset Nifty 50 ETF", AssetCategory.OTHER, "INF769K01FF7")
        stock = self.asset("ITC Limited", AssetCategory.STOCK, "INE154A01025")

        for fund in (category_fund, debt_fund, inf_only):
            self.assertTrue(is_mutual_fund_asset(fund), fund.name)
        for listed in (etf, legacy_named_etf, stock):
            self.assertFalse(is_mutual_fund_asset(listed), listed.name)

        loop_ids = {asset.id for asset in price_loop_assets()}
        self.assertNotIn(category_fund.id, loop_ids)
        self.assertNotIn(debt_fund.id, loop_ids)
        self.assertNotIn(inf_only.id, loop_ids)
        self.assertIn(etf.id, loop_ids)
        self.assertIn(legacy_named_etf.id, loop_ids)
        self.assertIn(stock.id, loop_ids)
        self.assertNotIn(bees_other.id, loop_ids)  # OTHER without an ETF name stays out, as before

        command_ids = set(market_price_assets(include_mutual_funds=False).values_list("id", flat=True))
        self.assertNotIn(debt_fund.id, command_ids)
        self.assertNotIn(inf_only.id, command_ids)

    def test_aif_unlisted_and_isin_less_bonds_are_left_out(self):
        aif = self.asset("Some Category III AIF", AssetCategory.STOCK, "INE000Z01011")
        self.transaction(aif, asset_class="Equity", sub_class="AIF")
        unlisted = self.asset("Pre IPO Co", AssetCategory.STOCK, "INE000Z01029")
        self.transaction(unlisted, asset_class="Equity", sub_class="Unlisted Shares")
        bond_without_isin = self.asset("Some NCD", AssetCategory.BOND, None)
        listed_bond = self.asset("Listed NCD", AssetCategory.BOND, "INE000Z07011")
        sgb = self.asset("SOVEREIGN GOLD BOND 2.50% 2031", AssetCategory.GOLD, "IN0020230085")

        loop_ids = {asset.id for asset in price_loop_assets()}
        self.assertNotIn(aif.id, loop_ids)
        self.assertNotIn(unlisted.id, loop_ids)
        self.assertNotIn(bond_without_isin.id, loop_ids)
        self.assertIn(listed_bond.id, loop_ids)
        self.assertIn(sgb.id, loop_ids)

    def test_stalest_prices_are_processed_first(self):
        fresh = self.asset("Fresh Ltd", AssetCategory.STOCK, "INE000F01011")
        old = self.asset("Old Ltd", AssetCategory.STOCK, "INE000O01011")
        never = self.asset("Never Ltd", AssetCategory.STOCK, "INE000N01011")
        legacy_null = self.asset("Legacy Ltd", AssetCategory.STOCK, "INE000L01011")
        self.price(fresh, date(2026, 11, 25), "10", fetched_at=ist(2026, 11, 25, 10, 0))
        self.price(old, date(2026, 11, 25), "10", fetched_at=ist(2026, 11, 25, 9, 20))
        self.price(legacy_null, date(2026, 11, 20), "10", fetched_at=None)

        ordered = [asset.id for asset in price_loop_assets()]
        self.assertEqual(ordered, [never.id, legacy_null.id, old.id, fresh.id])


class FundRoutingTests(PriceTestBase):
    def test_bond_categorised_fund_never_goes_to_the_bond_provider(self):
        fund = self.asset("BANDHAN Dynamic Bond Fund-Direct Plan-Growth", AssetCategory.BOND, "INF194K01Y29")
        self.transaction(fund, asset_class="Debt", sub_class="Mutual Fund")
        with (
            patch("market_data.services.market_data_manager.BondPriceService.get_latest_price") as bond,
            patch.object(MarketDataManager, "_fetch_amfi_nav", return_value={"success": True, "source": "AMFI"}) as nav,
        ):
            result = MarketDataManager.fetch_and_rebuild(fund)
        bond.assert_not_called()
        nav.assert_called_once()
        self.assertEqual(result["source"], "AMFI")

    def test_fund_without_isin_is_skipped_with_a_clear_reason(self):
        fund = self.asset("Old Fund Without ISIN", AssetCategory.BOND, None)
        self.transaction(fund, asset_class="Debt", sub_class="Mutual Fund")
        with (
            patch("market_data.services.market_data_manager.BondPriceService.get_latest_price") as bond,
            patch("market_data.services.market_data_manager.YahooFinanceService.save_history") as yahoo,
        ):
            result = MarketDataManager.fetch_and_rebuild(fund)
        bond.assert_not_called()
        yahoo.assert_not_called()
        self.assertTrue(result["skipped"])
        self.assertIn("no ISIN", result["reason"])


class MarketPricesJobTests(PriceTestBase):
    def setUp(self):
        super().setUp()
        from market_data.jobs import MarketPricesJob

        self.job = MarketPricesJob(calendar=CALENDAR, deadline_seconds=0.3, mis_reference=False)

    def test_is_current_outside_market_hours_when_every_close_is_stored(self):
        stock = self.asset("ITC Limited", AssetCategory.STOCK, "INE154A01025")
        self.price(stock, date(2026, 11, 25), "450", fetched_at=ist(2026, 11, 25, 16, 0))
        self.assertTrue(self.job.is_current(ist(2026, 11, 25, 20, 0), last_success=None, at_startup=True)[0])
        self.assertFalse(self.job.is_current(ist(2026, 11, 25, 11, 0), last_success=None, at_startup=True)[0])

    def test_startup_fetches_only_stale_assets(self):
        current = self.asset("Current Ltd", AssetCategory.STOCK, "INE000C01011")
        stale = self.asset("Stale Ltd", AssetCategory.STOCK, "INE000S01011")
        self.price(current, date(2026, 11, 25), "10", fetched_at=ist(2026, 11, 25, 16, 0))
        self.price(stale, date(2026, 11, 25), "10", fetched_at=ist(2026, 11, 25, 12, 0))
        seen = []

        def fetch(asset):
            seen.append(asset.id)
            return {"success": True, "skipped": False, "records": 1}

        with patch.object(MarketDataManager, "fetch_and_rebuild", side_effect=fetch):
            counts = self.job.run(ist(2026, 11, 25, 20, 0))
        self.assertEqual(seen, [stale.id])
        self.assertEqual(counts["current"], 1)
        self.assertEqual(counts["updated"], 1)

    def test_a_hanging_provider_costs_at_most_one_deadline(self):
        hung = self.asset("Bandhan Bank Limited", AssetCategory.STOCK, "INE545U01014")
        nxt = self.asset("Bank Of Baroda", AssetCategory.STOCK, "INE028A01039")
        release = threading.Event()
        processed = []

        def fetch(asset):
            if asset.id == hung.id:
                release.wait(10)
                return {"success": True}
            processed.append(asset.id)
            return {"success": True, "skipped": False, "records": 1}

        try:
            with patch.object(MarketDataManager, "fetch_and_rebuild", side_effect=fetch):
                started = time.monotonic()
                counts = self.job.run(ist(2026, 11, 25, 11, 0))
                elapsed = time.monotonic() - started
        finally:
            release.set()
        self.assertEqual(processed, [nxt.id])
        self.assertEqual(counts["timed_out"], 1)
        self.assertLess(elapsed, 2.0)

    def test_failures_are_summarised_not_logged_per_asset_at_warning(self):
        for index in range(5):
            self.asset(f"Broken {index} Ltd", AssetCategory.STOCK, f"INE00{index}B01011")
        with patch.object(MarketDataManager, "fetch_and_rebuild",
                          return_value={"success": False, "skipped": False, "error": "provider down"}):
            with self.assertLogs("market_data", level="WARNING") as logs:
                counts = self.job.run(ist(2026, 11, 25, 11, 0))
        self.assertEqual(counts["failed"], 5)
        self.assertEqual(len(logs.output), 1)
        self.assertIn("provider down", logs.output[0])

    def test_unresolvable_symbol_is_not_retried_every_cycle(self):
        stock = self.asset("Obscure Ltd", AssetCategory.STOCK, "INE000X01011")
        calls = []

        def fetch(asset):
            calls.append(asset.id)
            return {"success": False, "skipped": True, "symbol": None,
                    "reason": "Unable to resolve Yahoo Finance symbol for Obscure Ltd"}

        with patch.object(MarketDataManager, "fetch_and_rebuild", side_effect=fetch):
            self.job.run(ist(2026, 11, 25, 10, 0))
            self.job.run(ist(2026, 11, 25, 10, 15))
            self.assertEqual(calls, [stock.id])
            self.job.run(ist(2026, 11, 26, 10, 30))
        self.assertEqual(calls, [stock.id, stock.id])


class RepeatedFailureBackoffTests(PriceTestBase):
    """A symbol that keeps failing while the provider works for other assets
    (a stale or delisted Yahoo symbol) is retried every failed_retry_hours,
    not every 15 minutes. A provider-wide outage never triggers the backoff."""

    def setUp(self):
        super().setUp()
        import tempfile

        from django.test import override_settings

        from market_data.jobs import MarketPricesJob

        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        override = override_settings(PWMS_DATA_DIR=self._tmp.name)
        override.enable()
        self.addCleanup(override.disable)
        self.job = MarketPricesJob(calendar=CALENDAR, deadline_seconds=0, mis_reference=False)
        self.assertEqual((self.job.backoff.threshold, self.job.backoff.retry.total_seconds()), (3, 6 * 3600))
        self.good = self.asset("Good Bank Limited", AssetCategory.STOCK, "INE000G01011", symbol="GOOD.NS")
        self.bad = self.asset("IIFL Securities Limited", AssetCategory.STOCK, "INE489L01022", symbol="IIFLSEC.NS")
        self.calls = []
        self.bad_works = False
        self.good_works = True

    def _fetch(self, asset):
        self.calls.append(asset.id)
        works = self.good_works if asset.id == self.good.id else self.bad_works
        if works:
            return {"success": True, "skipped": False, "records": 1}
        return {"success": False, "skipped": False, "error": f"No market data for {asset.symbol}"}

    def _run(self, hour, minute):
        with patch.object(MarketDataManager, "fetch_and_rebuild", side_effect=self._fetch):
            return self.job.run(ist(2026, 11, 25, hour, minute))

    def _bad_calls(self):
        return self.calls.count(self.bad.id)

    def test_symbol_failing_while_others_update_backs_off_after_three_passes(self):
        self._run(10, 0)
        self._run(10, 15)
        with self.assertLogs("market_data", level="WARNING") as logs:
            self._run(10, 30)
        self.assertEqual(self._bad_calls(), 3)
        backoff_lines = [line for line in logs.output if "every 6" in line]
        self.assertEqual(len(backoff_lines), 1)
        self.assertIn("IIFLSEC.NS", backoff_lines[0])
        self.assertIn("price_failures.csv", backoff_lines[0])

        counts = self._run(10, 45)
        self.assertEqual(self._bad_calls(), 3)
        self.assertEqual(counts["failing_deferred"], 1)
        self.assertEqual(counts["updated"], 1)
        self._run(16, 29)
        self.assertEqual(self._bad_calls(), 3)
        self._run(16, 30)  # 6 hours after the third failure
        self.assertEqual(self._bad_calls(), 4)
        self._run(16, 45)  # failed again on its retry: back to the slow schedule at once
        self.assertEqual(self._bad_calls(), 4)

        import csv
        from pathlib import Path

        with (Path(self._tmp.name) / "reports" / "price_failures.csv").open(encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual([(row["asset_id"], row["symbol"]) for row in rows], [(str(self.bad.id), "IIFLSEC.NS")])
        self.assertIn("No market data", rows[0]["last_error"])

    def test_failures_after_the_close_still_back_off(self):
        # After the close only the broken symbol is stale; the provider worked
        # for everything else in the close pass, so the streak still counts.
        self._run(16, 0)
        self.price(self.good, date(2026, 11, 25), "10", fetched_at=ist(2026, 11, 25, 16, 0))
        self._run(16, 15)
        self._run(16, 30)
        self._run(16, 45)
        self.assertEqual(self._bad_calls(), 3)

    def test_provider_wide_outage_never_triggers_the_backoff(self):
        self.good_works = False
        for minute in (0, 15, 30, 45):
            self._run(11, minute)
        self.assertEqual(self._bad_calls(), 4)
        self.assertEqual(self.calls.count(self.good.id), 4)

    def test_a_success_resets_the_streak(self):
        self._run(10, 0)
        self._run(10, 15)
        self.bad_works = True
        self._run(10, 30)
        self.bad_works = False
        self._run(10, 45)
        self._run(11, 0)
        self._run(11, 15)
        self.assertEqual(self._bad_calls(), 6)

    def test_empty_result_and_missing_symbol_still_count_and_report_cleanly(self):
        self.bad.symbol = None
        self.bad.save(update_fields=["symbol"])

        def fetch(asset):
            self.calls.append(asset.id)
            return {"success": True, "records": 1} if asset.id == self.good.id else None

        with patch.object(MarketDataManager, "fetch_and_rebuild", side_effect=fetch):
            for minute in (0, 15, 30, 45):
                self.job.run(ist(2026, 11, 25, 10, minute))
        self.assertEqual(self._bad_calls(), 3)
        import csv
        from pathlib import Path

        with (Path(self._tmp.name) / "reports" / "price_failures.csv").open(encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(rows[0]["symbol"], "")
        self.assertEqual(rows[0]["last_error"], "unknown error")

    def test_report_locked_by_excel_warns_once_and_prices_still_update(self):
        with patch(
            "config.scheduling.files.atomic_write_text",
            side_effect=PermissionError(13, "The process cannot access the file"),
        ) as write:
            self._run(10, 0)
            self._run(10, 15)
            with self.assertLogs("market_data", level="WARNING") as logs:
                counts = self._run(10, 30)
        write.assert_called_once()
        self.assertEqual(counts["updated"], 1)  # the good asset still updated
        self.assertEqual(len([line for line in logs.output if "could not write" in line]), 1)
        self.assertEqual(self._run(10, 45)["failing_deferred"], 1)  # backoff kept in memory

    def test_no_report_file_while_nothing_is_backed_off(self):
        from pathlib import Path

        self._run(10, 0)
        self.assertFalse((Path(self._tmp.name) / "reports" / "price_failures.csv").exists())

    def test_threshold_zero_disables_the_backoff(self):
        self.job.backoff.threshold = 0
        for minute in (0, 15, 30, 45):
            self._run(10, minute)
        self.assertEqual(self._bad_calls(), 4)


class MISReferenceFailureTests(PriceTestBase):
    """MIS reference symbols: one summary line per refresh, and a reference that
    keeps failing stops forcing the refresh every 15 minutes after the close."""

    REFERENCES = {"Nifty 50": ("^NSEI",), "Broken InvIT": ("NHIT.NS", "NHIT.BO")}

    def setUp(self):
        super().setUp()
        from market_data.jobs import MarketPricesJob

        self.job = MarketPricesJob(calendar=CALENDAR, deadline_seconds=0, mis_reference=True)
        self.attempts = []

    def _save_history(self, *, asset, symbol, **kwargs):
        self.attempts.append(symbol)
        if symbol.startswith("NHIT"):
            raise ValueError(f"No market data for {symbol}")
        MarketPrice.objects.update_or_create(
            asset=asset, date=date(2026, 11, 25), source=DataSource.YAHOO_FINANCE,
            defaults={"close_price": Decimal("1"), "adjusted_close": Decimal("1"), "fetched_at": self._now},
        )
        return 1

    def _references(self):
        from contextlib import ExitStack

        from portfolio.mis_report_service import MISReportService

        stack = ExitStack()
        stack.enter_context(patch.object(MISReportService, "REFERENCE_SYMBOLS", self.REFERENCES))
        stack.enter_context(patch.object(MISReportService, "_custom_note_references", return_value={}))
        return stack

    def _refresh(self, hour, minute):
        self._now = ist(2026, 11, 25, hour, minute)
        with (
            self._references(),
            patch.object(YahooFinanceService, "save_history", side_effect=self._save_history),
            patch.object(MarketDataManager, "fetch_and_rebuild", return_value={"success": True, "records": 0}),
        ):
            return self.job.run(self._now)

    def test_failed_reference_logs_one_summary_line(self):
        from portfolio.mis_report_service import MISReportService

        with (
            patch.object(MISReportService, "REFERENCE_SYMBOLS", self.REFERENCES),
            patch.object(MISReportService, "_custom_note_references", return_value={}),
            patch.object(YahooFinanceService, "save_history", side_effect=lambda **kw: self._save_history(**kw)),
            self.assertLogs("portfolio", level="DEBUG") as logs,
        ):
            self._now = ist(2026, 11, 25, 11, 0)
            result = MISReportService.refresh_reference_prices()
        warnings = [line for line in logs.output if line.startswith("WARNING")]
        self.assertEqual(len(warnings), 1)
        self.assertIn("Broken InvIT", warnings[0])
        self.assertIn("NHIT.NS", warnings[0])
        self.assertEqual((result["references"], result["refreshed"], result["failed"]), (2, 1, 1))
        self.assertEqual(result["failures"][0]["symbols"], ["NHIT.NS", "NHIT.BO"])

    def test_exclude_symbols_skips_a_reference_without_counting_it_failed(self):
        from portfolio.mis_report_service import MISReportService

        self._now = ist(2026, 11, 25, 11, 0)
        with (
            patch.object(MISReportService, "REFERENCE_SYMBOLS", self.REFERENCES),
            patch.object(MISReportService, "_custom_note_references", return_value={}),
            patch.object(YahooFinanceService, "save_history", side_effect=lambda **kw: self._save_history(**kw)),
        ):
            result = MISReportService.refresh_reference_prices(exclude_symbols={"NHIT.NS", "NHIT.BO"})
        self.assertNotIn("NHIT.NS", self.attempts)
        self.assertEqual((result["refreshed"], result["failed"], result["deferred"]), (1, 0, 1))

    def test_after_the_close_a_failing_reference_stops_forcing_refreshes(self):
        with self.assertLogs("portfolio", level="WARNING"):
            for minute in (0, 15, 30):
                self._refresh(16, minute)
        self.assertEqual(self.attempts.count("NHIT.NS"), 3)
        with self._references():
            self.assertFalse(self.job._mis_references_stale(ist(2026, 11, 25, 16, 45)))
        self._refresh(16, 45)
        self.assertEqual(self.attempts.count("NHIT.NS"), 3)
        self._refresh(17, 0)
        self.assertEqual(self.attempts.count("NHIT.NS"), 3)
        # Retried once the 6-hour backoff from the 16:30 failure is over.
        with self.assertLogs("portfolio", level="WARNING"):
            self._refresh(22, 30)
        self.assertEqual(self.attempts.count("NHIT.NS"), 4)


class BondProviderTests(SimpleTestCase):
    def setUp(self):
        from market_data.services.bond_price_service import BondPriceService

        self.service = BondPriceService
        self.service.reset_state()
        self.addCleanup(self.service.reset_state)

    def _response(self, status, payload=None):
        import requests

        response = requests.Response()
        response.status_code = status
        response._content = (b"[]" if payload is None else __import__("json").dumps(payload).encode())
        response.url = "https://example.invalid"
        return response

    def test_requests_use_connect_and_read_timeouts(self):
        with patch("market_data.services.bond_price_service.requests.post", return_value=self._response(200, [])) as post:
            self.service.get_latest_price("INE000Z07011")
        timeout = post.call_args.kwargs["timeout"]
        self.assertIsInstance(timeout, tuple)
        self.assertEqual(len(timeout), 2)

    def test_one_download_serves_every_bond_in_a_cycle(self):
        row = [None, "INE000Z07011", None, "Issuer", None, "Desc", "101.25", "7.1", None, None, None, "100",
               "24-11-2026 11:00:00", "25-11-2026"]
        with patch("market_data.services.bond_price_service.requests.post", return_value=self._response(200, [row])) as post:
            first = self.service.get_latest_price("INE000Z07011")
            second = self.service.get_latest_price("INE111Z07011")
        self.assertEqual(post.call_count, 1)
        self.assertEqual(first["price"], Decimal("101.25"))
        self.assertIsNone(second)

    def test_http_400_disables_the_provider_with_a_clear_status(self):
        from market_data.services.bond_price_service import BondProviderUnavailable

        with patch("market_data.services.bond_price_service.requests.post", return_value=self._response(400)) as post:
            with self.assertRaises(BondProviderUnavailable) as first:
                self.service.get_latest_price("INE000Z07011")
            with self.assertRaises(BondProviderUnavailable):
                self.service.get_latest_price("INE111Z07011")
        self.assertEqual(post.call_count, 1)
        self.assertIn("HTTP 400", str(first.exception))
        self.assertIn("retry after", str(first.exception))


class RefetchRecentPricesCommandTests(PriceTestBase):
    def test_refetches_the_last_n_days_for_yahoo_priced_assets_only(self):
        from io import StringIO

        from django.core.management import call_command

        stock = self.asset("ITC Limited", AssetCategory.STOCK, "INE154A01025", "ITC.NS")
        self.price(stock, date(2026, 11, 20), "450", fetched_at=None)
        fund = self.asset("Some Fund", AssetCategory.MUTUAL_FUND, "INF000A01001")
        sgb = self.asset("SOVEREIGN GOLD BOND 2031", AssetCategory.GOLD, "IN0020230085")
        calls = []

        def save_history(*, asset, symbol, start, end):
            calls.append((asset.id, symbol, start, end))
            return 3

        with (
            patch.object(MarketDataManager, "resolve_asset_symbol", return_value="ITC.NS"),
            patch.object(YahooFinanceService, "save_history", side_effect=save_history),
            patch.object(MarketDataManager, "_rebuild_holding", return_value=None),
            patch("market_data.management.commands.refetch_recent_prices.price_now", return_value=ist(2026, 11, 25, 20, 0)),
        ):
            out = StringIO()
            call_command("refetch_recent_prices", days=30, stdout=out)

        self.assertEqual(calls, [(stock.id, "ITC.NS", date(2026, 10, 26), date(2026, 11, 26))])
        self.assertNotIn(fund.id, [call[0] for call in calls])
        self.assertNotIn(sgb.id, [call[0] for call in calls])
        self.assertIn("records=3", out.getvalue())

    def test_dry_run_fetches_nothing(self):
        from io import StringIO

        from django.core.management import call_command

        self.asset("ITC Limited", AssetCategory.STOCK, "INE154A01025", "ITC.NS")
        with patch.object(YahooFinanceService, "save_history") as save_history:
            out = StringIO()
            call_command("refetch_recent_prices", days=30, dry_run=True, stdout=out)
        save_history.assert_not_called()
        self.assertIn("ITC Limited", out.getvalue())
