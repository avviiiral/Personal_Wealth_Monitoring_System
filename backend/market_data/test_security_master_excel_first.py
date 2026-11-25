"""Security Master: Excel first, write only newly resolved symbols."""
import csv
import os
import tempfile
from datetime import date
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import openpyxl
from django.contrib.auth.models import User
from django.test import SimpleTestCase, TestCase, override_settings

from investments.models import Asset, AssetCategory, Transaction
from market_data.services.market_data_manager import MarketDataManager
from market_data.services.security_master_generator import SecurityMasterGenerator
from market_data.services.security_resolver import SecurityResolver
from market_data.services.yahoo_finance import YahooFinanceService
from users.models import FamilyGroup

RESOLVER_STATE = [name for name in vars(SecurityResolver) if name.startswith("_security_master_") or name in (
    "_loaded_path", "_loaded_mtime", "_pending_writes", "_unresolved", "_load_conflicts", "_write_warned",
    "_nse_cache", "_nse_cache_expires", "_ambiguous_name_warned",
)]

HDFC_ISIN = "INE040A01034"
NEW_ISIN = "INE999A01011"
SAVED_RECORDS = 3


def write_workbook(path, rows):
    workbook = openpyxl.Workbook()
    worksheet = workbook.active
    worksheet.title = "Security Master"
    worksheet.append(SecurityMasterGenerator.OUTPUT_COLUMNS)
    for isin, name, symbol in rows:
        record = {column: None for column in SecurityMasterGenerator.OUTPUT_COLUMNS}
        record.update({"ISIN": isin, "Security Name": name, "Yahoo Symbol": symbol})
        worksheet.append([record[column] for column in SecurityMasterGenerator.OUTPUT_COLUMNS])
    workbook.save(path)
    workbook.close()


def read_rows(path):
    workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
    worksheet = workbook["Security Master"]
    rows = list(worksheet.iter_rows(values_only=True))
    workbook.close()
    headers = rows[0]
    return [dict(zip(headers, row)) for row in rows[1:]]


class IsolatedWorkbookMixin:
    def isolate(self):
        saved = {name: getattr(SecurityResolver, name, None) for name in RESOLVER_STATE}
        self.addCleanup(lambda: [setattr(SecurityResolver, name, value) for name, value in saved.items()])
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.data_dir = Path(self._tmp.name)
        override = override_settings(PWMS_DATA_DIR=str(self.data_dir))
        override.enable()
        self.addCleanup(override.disable)
        self.workbook = self.data_dir / "security_master.xlsx"
        SecurityResolver.reset_cache()


class NameNormalizationTests(SimpleTestCase):
    def test_equivalent_legal_names_normalize_identically(self):
        normalize = SecurityResolver.normalize_security_name
        self.assertEqual(normalize("Larsen & Toubro Ltd."), normalize("LARSEN AND TOUBRO LIMITED"))
        self.assertEqual(normalize("Bharat Electronics (I) Ltd"), normalize("Bharat Electronics India Limited"))
        self.assertEqual(normalize("  hdfc   bank-ltd "), normalize("HDFC BANK LIMITED"))
        self.assertNotEqual(normalize("HDFC Bank"), normalize("HDFC Life"))


class ExcelFirstResolutionTests(IsolatedWorkbookMixin, TestCase):
    def setUp(self):
        self.isolate()
        self.user = User.objects.create_user(username="resolver_owner", password="pw")
        self.family = FamilyGroup.objects.create(name="Resolver Family")

    def _asset(self, name, isin=None, symbol=None):
        asset = Asset.objects.create(owner=self.user, family=self.family, name=name, category=AssetCategory.STOCK,
                                     isin=isin, symbol=symbol, currency="INR", is_active=True)
        Transaction.objects.create(
            owner=self.user, family=self.family, family_name="M", portfolio="P", asset_class="Equity",
            sub_class="Direct Equity", asset_name=name, underlying="", asset=asset, transaction_type="BUY",
            transaction_date=date(2026, 1, 5), quantity=Decimal("1"), price_per_unit=Decimal("1"),
            amount=Decimal("1"), fees=Decimal("0"),
        )
        return asset

    def _fetch(self, asset):
        holding = SimpleNamespace(id=1, current_price=Decimal("1"), current_value=Decimal("1"))
        with patch.object(YahooFinanceService, "save_history", return_value=SAVED_RECORDS), \
                patch.object(MarketDataManager, "_rebuild_holding", return_value=holding):
            return MarketDataManager.fetch_and_rebuild(asset)

    def test_isin_hit_uses_the_workbook_and_writes_nothing(self):
        write_workbook(self.workbook, [(HDFC_ISIN, "HDFC Bank Limited", "HDFCBANK.NS")])
        before = self.workbook.stat().st_mtime_ns
        asset = self._asset("HDFC Bank Ltd", isin=HDFC_ISIN)
        with patch.object(SecurityMasterGenerator, "persist_resolved_symbol") as persist, \
                patch.object(SecurityResolver, "_search_yahoo") as search:
            result = self._fetch(asset)
        self.assertEqual(result["symbol"], "HDFCBANK.NS")
        persist.assert_not_called()
        search.assert_not_called()
        self.assertEqual(self.workbook.stat().st_mtime_ns, before)

    def test_exact_name_hit_for_an_asset_without_isin_writes_nothing(self):
        write_workbook(self.workbook, [(HDFC_ISIN, "HDFC Bank Limited", "HDFCBANK.NS")])
        asset = self._asset("HDFC BANK LTD.")
        with patch.object(SecurityMasterGenerator, "persist_resolved_symbol") as persist:
            result = self._fetch(asset)
        self.assertEqual(result["symbol"], "HDFCBANK.NS")
        persist.assert_not_called()

    def test_miss_resolves_then_saves_the_row_without_a_full_reload(self):
        write_workbook(self.workbook, [(HDFC_ISIN, "HDFC Bank Limited", "HDFCBANK.NS")])
        asset = self._asset("New Listing Ltd", isin=NEW_ISIN, symbol="NEWLIST")
        SecurityResolver.lookup_workbook(isin=HDFC_ISIN)  # first load
        with patch.object(SecurityResolver, "_read_workbook", wraps=SecurityResolver._read_workbook) as reads:
            result = self._fetch(asset)
            self.assertEqual(result["symbol"], "NEWLIST.NS")
            # The in-memory cache already knows the new mapping...
            self.assertEqual(SecurityResolver.lookup_workbook(isin=NEW_ISIN), "NEWLIST.NS")
        # ...and the workbook was never re-read.
        reads.assert_not_called()
        rows = {row["ISIN"]: row for row in read_rows(self.workbook)}
        self.assertEqual(rows[NEW_ISIN]["Yahoo Symbol"], "NEWLIST.NS")
        self.assertEqual(rows[NEW_ISIN]["Security Name"], "New Listing Ltd")
        self.assertEqual(rows[HDFC_ISIN]["Yahoo Symbol"], "HDFCBANK.NS")

    def test_resolved_symbol_is_not_saved_when_no_price_data_comes_back(self):
        asset = self._asset("New Listing Ltd", isin=NEW_ISIN, symbol="NEWLIST")
        with patch.object(YahooFinanceService, "save_history", side_effect=ValueError("No market data")), \
                patch.object(MarketDataManager, "_rebuild_holding", return_value=None):
            MarketDataManager.fetch_and_rebuild(asset)
        self.assertFalse(self.workbook.exists())
        self.assertIsNone(SecurityResolver.lookup_workbook(isin=NEW_ISIN))

    def test_name_only_asset_is_saved_with_a_blank_isin(self):
        asset = self._asset("Tiny Listed Co Ltd")
        quotes = [{"symbol": "TINY.NS", "longname": "Tiny Listed Co Limited"}]
        with patch.object(SecurityResolver, "_search_yahoo", return_value=quotes), \
                patch.object(SecurityResolver, "nse_equity_list", return_value={}):
            result = self._fetch(asset)
        self.assertEqual(result["symbol"], "TINY.NS")
        rows = read_rows(self.workbook)
        self.assertEqual(len(rows), 1)
        self.assertIn(rows[0]["ISIN"], (None, ""))
        self.assertEqual(rows[0]["Yahoo Symbol"], "TINY.NS")

    def test_yahoo_result_with_a_different_name_is_rejected_and_reported(self):
        asset = self._asset("Tiny Listed Co Ltd")
        quotes = [{"symbol": "TINYX.NS", "longname": "Tiny Listed Holdings Limited"}]
        with patch.object(SecurityResolver, "_search_yahoo", return_value=quotes), \
                patch.object(SecurityResolver, "nse_equity_list", return_value={}):
            result = self._fetch(asset)
        self.assertFalse(result["success"])
        self.assertFalse(self.workbook.exists())
        with (self.data_dir / "reports" / "security_master_report.csv").open(encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual([(row["type"], row["name"]) for row in rows], [("needs_isin", "Tiny Listed Co Ltd")])

    def test_nse_equity_list_resolves_an_isin_miss(self):
        asset = self._asset("Listed By Isin Ltd", isin=NEW_ISIN)
        with patch.object(SecurityResolver, "nse_equity_list", return_value={NEW_ISIN: {"symbol": "LBI", "name": "LISTED BY ISIN LTD"}}), \
                patch.object(SecurityResolver, "_search_yahoo") as search:
            result = self._fetch(asset)
        search.assert_not_called()
        self.assertEqual(result["symbol"], "LBI.NS")
        self.assertEqual(SecurityResolver.lookup_workbook(isin=NEW_ISIN), "LBI.NS")


class LockedWorkbookTests(IsolatedWorkbookMixin, SimpleTestCase):
    def setUp(self):
        self.isolate()
        write_workbook(self.workbook, [(HDFC_ISIN, "HDFC Bank Limited", "HDFCBANK.NS")])

    def test_locked_workbook_queues_the_write_keeps_memory_and_warns_once(self):
        with patch("market_data.services.security_master_generator.replace_with_retry",
                   side_effect=PermissionError(32, "being used by another process")):
            with self.assertLogs("market_data", level="WARNING") as logs:
                SecurityResolver.remember(isin="INE111A01011", name="First Ltd", symbol="FIRST.NS")
                SecurityResolver.remember(isin="INE222A01011", name="Second Ltd", symbol="SECOND.NS")
        self.assertEqual(len([line for line in logs.output if "locked" in line.lower()]), 1)
        self.assertEqual(SecurityResolver.lookup_workbook(isin="INE111A01011"), "FIRST.NS")
        self.assertEqual(SecurityResolver.pending_write_count(), 2)
        self.assertNotIn("INE111A01011", {row["ISIN"] for row in read_rows(self.workbook)})

        written = SecurityResolver.flush_pending_writes(force=True)
        self.assertEqual(written, 2)
        self.assertEqual(SecurityResolver.pending_write_count(), 0)
        isins = {row["ISIN"] for row in read_rows(self.workbook)}
        self.assertTrue({"INE111A01011", "INE222A01011"} <= isins)

    def test_pending_mappings_survive_a_reload_after_an_external_edit(self):
        with patch("market_data.services.security_master_generator.replace_with_retry",
                   side_effect=PermissionError(32, "locked")):
            with self.assertLogs("market_data", level="WARNING"):
                SecurityResolver.remember(isin="INE111A01011", name="First Ltd", symbol="FIRST.NS")
        write_workbook(self.workbook, [(HDFC_ISIN, "HDFC Bank Limited", "HDFCBANK.NS"), ("INE333A01011", "Third Ltd", "THIRD.NS")])
        os.utime(self.workbook, ns=(self.workbook.stat().st_atime_ns, self.workbook.stat().st_mtime_ns + 10_000_000))
        self.assertEqual(SecurityResolver.lookup_workbook(isin="INE333A01011"), "THIRD.NS")
        self.assertEqual(SecurityResolver.lookup_workbook(isin="INE111A01011"), "FIRST.NS")


class LoadOnceAndConflictTests(IsolatedWorkbookMixin, SimpleTestCase):
    def setUp(self):
        self.isolate()

    def test_workbook_is_loaded_once_and_reloaded_only_when_modified(self):
        write_workbook(self.workbook, [(HDFC_ISIN, "HDFC Bank Limited", "HDFCBANK.NS")])
        with patch.object(SecurityResolver, "_read_workbook", wraps=SecurityResolver._read_workbook) as reads:
            for _ in range(5):
                SecurityResolver.lookup_workbook(isin=HDFC_ISIN)
            self.assertEqual(reads.call_count, 1)
            write_workbook(self.workbook, [(HDFC_ISIN, "HDFC Bank Limited", "HDFCBANK.BO")])
            os.utime(self.workbook, ns=(self.workbook.stat().st_atime_ns, self.workbook.stat().st_mtime_ns + 10_000_000))
            self.assertEqual(SecurityResolver.lookup_workbook(isin=HDFC_ISIN), "HDFCBANK.BO")
            self.assertEqual(reads.call_count, 2)

    def test_missing_workbook_is_not_generated_by_a_lookup(self):
        with patch.object(SecurityResolver, "_generate_security_master") as generate:
            self.assertIsNone(SecurityResolver.lookup_workbook(isin=HDFC_ISIN))
        generate.assert_not_called()

    def test_conflicts_log_one_summary_line_and_go_to_the_csv_report(self):
        rows = [(f"INE00{index}C01011", f"Conflict {index} Ltd", f"C{index}A.NS") for index in range(12)]
        rows += [(f"INE00{index}C01011", f"Conflict {index} Ltd", f"C{index}B.NS") for index in range(12)]
        write_workbook(self.workbook, rows)
        with self.assertLogs("market_data", level="INFO") as logs:
            SecurityResolver.reload_if_changed()
        with self.assertRaises(ValueError):
            SecurityResolver.lookup_workbook(isin="INE000C01011")  # quarantined, never guessed
        conflict_lines = [line for line in logs.output if "conflict" in line.lower()]
        self.assertEqual(len(conflict_lines), 1)
        self.assertIn("12", conflict_lines[0])
        self.assertIn("INE000C01011", conflict_lines[0])
        self.assertNotIn("INE0011C01011", conflict_lines[0])  # only the first 10 are named
        with (self.data_dir / "reports" / "security_master_report.csv").open(encoding="utf-8") as handle:
            report = [row for row in csv.DictReader(handle) if row["type"] == "conflict"]
        self.assertEqual(len({row["key"] for row in report if row["identifier"] == "ISIN"}), 12)

    def test_ambiguous_name_is_warned_once_per_process_not_every_cycle(self):
        write_workbook(self.workbook, [
            ("INE155A01022", "Tata Motors Limited", "TATAMOTORS.NS"),
            ("INE1TAE01010", "Tata Motors Limited", "TMCV.NS"),
        ])
        with self.assertLogs("market_data", level="DEBUG") as logs:
            for _ in range(5):
                self.assertIsNone(SecurityResolver.resolve_from_name("Tata Motors Limited"))
        warnings = [line for line in logs.output if line.startswith("WARNING") and "ambiguous" in line.lower()]
        self.assertEqual(len(warnings), 1)
        self.assertIn("TATA MOTORS", warnings[0].upper())

    def test_correcting_a_row_in_excel_takes_effect_on_the_next_lookup(self):
        write_workbook(self.workbook, [(HDFC_ISIN, "HDFC Bank Limited", "WRONG.NS")])
        self.assertEqual(SecurityResolver.lookup_workbook(isin=HDFC_ISIN), "WRONG.NS")
        write_workbook(self.workbook, [(HDFC_ISIN, "HDFC Bank Limited", "HDFCBANK.NS")])
        os.utime(self.workbook, ns=(self.workbook.stat().st_atime_ns, self.workbook.stat().st_mtime_ns + 10_000_000))
        self.assertEqual(SecurityResolver.lookup_workbook(isin=HDFC_ISIN), "HDFCBANK.NS")
