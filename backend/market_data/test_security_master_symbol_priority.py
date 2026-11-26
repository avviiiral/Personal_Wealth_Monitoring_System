"""The Security Master (security_master.xlsx) symbol always wins.

When the workbook maps a security (by ISIN, or by exact normalized name for a
row whose ISIN is blank or matches), that Yahoo symbol is used no matter what
Asset.symbol, the exchange, the configured name/ISIN maps or the NSE list
say. A stale Asset.symbol (for example IIFLSEC.NS after IIFL Securities
became IIFL Capital Services, NSE: IIFLCAPS) is corrected to the workbook
symbol by the price refresh instead of blocking it as a CONFLICT.

Only a workbook that disagrees with itself (two rows, two symbols for one
ISIN) still fails closed.
"""
import json
from datetime import date
from decimal import Decimal
from io import StringIO
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import SimpleTestCase, TestCase

from investments.models import Asset, AssetCategory, Transaction
from market_data.services.market_data_manager import MarketDataManager
from market_data.services.security_master_generator import SecurityMasterGenerator
from market_data.services.security_resolver import SecurityResolver
from market_data.services.security_resolver_diagnostics import STATUS_MATCHED
from market_data.services.yahoo_finance import YahooFinanceService
from market_data.test_security_master_excel_first import IsolatedWorkbookMixin, write_workbook
from users.models import FamilyGroup

IIFL_ISIN = "INE489L01022"
IIFL_NAME = "IIFL CAPITAL SERVICES LIMITED"
IIFL_MASTER = "IIFLCAPS.NS"
IIFL_STALE = "IIFLSEC.NS"

# Fictional identifiers for the in-memory tests (not in pwms_config.json).
ISIN_A = "INE0PRIO0011"
ISIN_B = "INE0PRIO0022"


class SecurityMasterPriorityUnitTests(SimpleTestCase):
    """resolve_yahoo_symbol / candidate_symbols against in-memory maps."""

    STATE = [name for name in vars(SecurityResolver) if name.startswith("_security_master_")] + [
        "_loaded_path", "_loaded_mtime", "_pending_writes",
    ]

    def setUp(self):
        saved = {name: getattr(SecurityResolver, name) for name in self.STATE}
        self.addCleanup(lambda: [setattr(SecurityResolver, k, v) for k, v in saved.items()])
        SecurityResolver._empty_maps()
        SecurityResolver._security_master_override_logged = set()
        SecurityResolver._pending_writes = {}
        SecurityResolver._security_master_loaded = True
        SecurityResolver._loaded_path = None
        SecurityResolver._loaded_mtime = None
        path_patch = patch.object(SecurityResolver, "_security_master_path", return_value=None)
        path_patch.start()
        self.addCleanup(path_patch.stop)

    def _master(self, isin, name, symbol):
        SecurityResolver._index_mapping(isin, symbol, name)

    def test_master_isin_symbol_overrides_stale_asset_symbol(self):
        self._master(IIFL_ISIN, IIFL_NAME, IIFL_MASTER)
        self.assertEqual(
            SecurityResolver.resolve_yahoo_symbol(symbol=IIFL_STALE, isin=IIFL_ISIN, name=IIFL_NAME),
            IIFL_MASTER,
        )

    def test_master_isin_symbol_overrides_bare_symbol_and_exchange(self):
        self._master(ISIN_A, "Prio Alpha Ltd", "ALPHA.NS")
        self.assertEqual(
            SecurityResolver.resolve_yahoo_symbol(symbol="OLDALPHA", exchange="BSE", isin=ISIN_A),
            "ALPHA.NS",
        )

    def test_master_isin_symbol_overrides_configured_isin_and_name_maps(self):
        self._master(ISIN_A, "Prio Alpha Ltd", "ALPHA.NS")
        with patch.dict(SecurityResolver.ISIN_TO_YAHOO, {ISIN_A: "CONFIGURED.NS"}), \
                patch.dict(SecurityResolver.NAME_TO_YAHOO, {"SOME OTHER NAME": "BYNAME.NS"}):
            self.assertEqual(
                SecurityResolver.resolve_yahoo_symbol(symbol="X", isin=ISIN_A, name="Some Other Name"),
                "ALPHA.NS",
            )

    def test_master_isin_row_beats_master_name_row_of_another_security(self):
        self._master(ISIN_A, "Prio Alpha Ltd", "ALPHA.NS")
        self._master(ISIN_B, "Prio Beta Ltd", "BETA.NS")
        # Asset carries ISIN A but the name of B: the ISIN row is authoritative.
        self.assertEqual(
            SecurityResolver.resolve_yahoo_symbol(isin=ISIN_A, name="Prio Beta Limited"),
            "ALPHA.NS",
        )

    def test_master_name_symbol_overrides_explicit_symbol_without_isin(self):
        self._master(None, "Prio Gamma Ltd", "GAMMA.NS")
        self.assertEqual(
            SecurityResolver.resolve_yahoo_symbol(symbol="OLDGAMMA.NS", name="PRIO GAMMA LIMITED"),
            "GAMMA.NS",
        )

    def test_master_name_row_with_another_isin_is_not_used(self):
        # The name row belongs to ISIN B; an asset with ISIN A is a different
        # security, so its own explicit symbol is used.
        self._master(ISIN_B, "Prio Delta Ltd", "DELTA.NS")
        self.assertEqual(
            SecurityResolver.resolve_yahoo_symbol(symbol="MINE.NS", isin=ISIN_A, name="Prio Delta Ltd"),
            "MINE.NS",
        )

    def test_name_on_several_isin_rows_is_not_used_for_an_asset_with_another_isin(self):
        # e.g. old and new ISIN rows after a split: the name -> ISIN mapping is
        # quarantined, so the name cannot prove the asset is that security.
        self._master(ISIN_A, "Prio Split Ltd", "SPLIT.NS")
        self._master(ISIN_B, "Prio Split Ltd", "SPLIT.NS")
        other_isin = "INE0PRIO0077"
        self.assertIsNone(SecurityResolver.lookup_workbook(isin=other_isin, name="Prio Split Ltd"))
        self.assertEqual(
            SecurityResolver.resolve_yahoo_symbol(symbol="MINE.NS", isin=other_isin, name="Prio Split Ltd"),
            "MINE.NS",
        )
        # Without an ISIN the unambiguous name -> symbol mapping still applies.
        self.assertEqual(SecurityResolver.lookup_workbook(name="Prio Split Ltd"), "SPLIT.NS")

    def test_conflicting_master_rows_still_fail_closed(self):
        self._master(ISIN_A, "Prio Alpha Ltd", "ALPHA1.NS")
        self._master(ISIN_A, "Prio Alpha Ltd", "ALPHA2.NS")
        with self.assertRaisesRegex(ValueError, "conflicting Security Master mappings"):
            SecurityResolver.resolve_yahoo_symbol(symbol="ALPHA1.NS", isin=ISIN_A)

    def test_configured_map_conflict_without_master_row_is_still_rejected(self):
        with patch.dict(SecurityResolver.ISIN_TO_YAHOO, {ISIN_A: "CONFIGURED.NS"}):
            with self.assertRaisesRegex(ValueError, "Conflicting security identifiers"):
                SecurityResolver.resolve_yahoo_symbol(symbol="OTHER", isin=ISIN_A)

    def test_queued_master_mapping_counts_as_master(self):
        SecurityResolver._pending_writes = {ISIN_A: (ISIN_A, "Prio Alpha Ltd", "ALPHA.NS")}
        SecurityResolver._apply_pending_to_memory()
        self.assertEqual(SecurityResolver.resolve_yahoo_symbol(symbol="STALE.NS", isin=ISIN_A), "ALPHA.NS")

    def test_resolve_for_asset_reports_workbook_symbol(self):
        self._master(IIFL_ISIN, IIFL_NAME, IIFL_MASTER)
        self.assertEqual(
            SecurityResolver.resolve_for_asset(symbol=IIFL_STALE, isin=IIFL_ISIN, name=IIFL_NAME),
            (IIFL_MASTER, True),
        )

    def test_candidate_symbols_put_master_symbol_first(self):
        self._master(ISIN_A, "Prio Alpha Ltd", "ALPHA.NS")
        candidates = SecurityResolver.candidate_symbols(symbol="OLDALPHA", isin=ISIN_A, name="Prio Alpha Ltd")
        self.assertEqual(candidates[0], "ALPHA.NS")
        self.assertIn("OLDALPHA.NS", candidates)

    def test_override_is_logged_once_per_security(self):
        self._master(IIFL_ISIN, IIFL_NAME, IIFL_MASTER)
        with self.assertLogs("market_data.services.security_resolver", level="INFO") as logs:
            for _ in range(3):
                SecurityResolver.resolve_yahoo_symbol(symbol=IIFL_STALE, isin=IIFL_ISIN, name=IIFL_NAME)
        overrides = [line for line in logs.output if "overrides" in line]
        self.assertEqual(len(overrides), 1)
        self.assertIn(IIFL_MASTER, overrides[0])
        self.assertIn(IIFL_STALE, overrides[0])

    def test_matching_asset_symbol_is_not_logged(self):
        self._master(ISIN_A, "Prio Alpha Ltd", "ALPHA.NS")
        with patch("market_data.services.security_resolver.logger") as log:
            SecurityResolver.resolve_yahoo_symbol(symbol="ALPHA", isin=ISIN_A)
            SecurityResolver.resolve_yahoo_symbol(symbol="ALPHA.NS", isin=ISIN_A)
        log.info.assert_not_called()


class SecurityMasterPriorityRefreshTests(IsolatedWorkbookMixin, TestCase):
    """The price refresh and the diagnostic against a real workbook."""

    def setUp(self):
        self.isolate()
        self.user = User.objects.create_user(username="priority_owner", password="pw")
        self.family = FamilyGroup.objects.create(name="Priority Family")
        write_workbook(self.workbook, [
            (IIFL_ISIN, "IIFL Capital Services Limited", IIFL_MASTER),
            (None, "Prio Name Only Ltd", "NAMEONLY.NS"),
        ])

    def _asset(self, name, *, isin=None, symbol=None):
        asset = Asset.objects.create(
            owner=self.user, family=self.family, name=name, category=AssetCategory.STOCK,
            isin=isin, symbol=symbol, currency="INR", is_active=True,
        )
        Transaction.objects.create(
            owner=self.user, family=self.family, family_name="M", portfolio="P", asset_class="Equity",
            sub_class="Direct Equity", asset_name=name, underlying="", asset=asset, transaction_type="BUY",
            transaction_date=date(2026, 1, 5), quantity=Decimal("1"), price_per_unit=Decimal("1"),
            amount=Decimal("1"), fees=Decimal("0"),
        )
        return asset

    def _fetch(self, asset):
        holding = SimpleNamespace(id=1, current_price=Decimal("1"), current_value=Decimal("1"))
        with patch.object(YahooFinanceService, "save_history", return_value=3) as save_history, \
                patch.object(MarketDataManager, "_rebuild_holding", return_value=holding), \
                patch.object(SecurityMasterGenerator, "persist_resolved_symbol") as persist:
            result = MarketDataManager.fetch_and_rebuild(asset)
        persist.assert_not_called()  # a workbook hit never writes the workbook
        return result, save_history

    def test_refresh_prices_with_master_symbol_and_corrects_stale_asset_symbol(self):
        asset = self._asset(IIFL_NAME, isin=IIFL_ISIN, symbol=IIFL_STALE)
        before = self.workbook.stat().st_mtime_ns

        result, save_history = self._fetch(asset)

        self.assertTrue(result["success"], result)
        self.assertEqual(result["symbol"], IIFL_MASTER)
        self.assertTrue(save_history.called)
        self.assertEqual({call.kwargs["symbol"] for call in save_history.call_args_list}, {IIFL_MASTER})
        asset.refresh_from_db()
        self.assertEqual(asset.symbol, IIFL_MASTER)
        self.assertEqual(self.workbook.stat().st_mtime_ns, before)

    def test_refresh_corrects_symbol_for_name_only_master_row(self):
        asset = self._asset("PRIO NAME ONLY LIMITED", symbol="OLDNAME.NS")
        result, _ = self._fetch(asset)
        self.assertEqual(result["symbol"], "NAMEONLY.NS")
        asset.refresh_from_db()
        self.assertEqual(asset.symbol, "NAMEONLY.NS")

    def test_non_master_symbol_still_never_overwrites_explicit_asset_symbol(self):
        asset = self._asset("Prio Unlisted Elsewhere Ltd", isin="INE0PRIO0099", symbol="CUSTOM.NS")
        resolved = MarketDataManager.resolve_asset_symbol(asset)
        self.assertEqual(resolved, "CUSTOM.NS")
        asset.refresh_from_db()
        self.assertEqual(asset.symbol, "CUSTOM.NS")

    def test_diagnostic_reports_matched_with_override_note_and_writes_nothing(self):
        asset = self._asset(IIFL_NAME, isin=IIFL_ISIN, symbol=IIFL_STALE)
        output = StringIO()
        call_command("diagnose_security_resolver", "--isin", IIFL_ISIN, "--format", "json", stdout=output)
        payload = json.loads(output.getvalue())

        self.assertEqual(payload["summary"]["CONFLICT"], 0)
        self.assertEqual(payload["summary"]["MATCHED"], 1)
        row = payload["rows"][0]
        self.assertEqual(row["status"], STATUS_MATCHED)
        self.assertEqual(row["database_symbol"], IIFL_STALE)
        self.assertEqual(row["security_master_symbol"], IIFL_MASTER)
        self.assertEqual(row["resolved_symbol"], IIFL_MASTER)
        self.assertIn(f"overrides Asset.symbol {IIFL_STALE}", row["reason"])
        asset.refresh_from_db()
        self.assertEqual(asset.symbol, IIFL_STALE)  # diagnostic is read-only

        table = StringIO()
        call_command("diagnose_security_resolver", "--isin", IIFL_ISIN, "--only-issues", stdout=table)
        self.assertIn("MATCHED=1", table.getvalue())
        self.assertIn("CONFLICT=0", table.getvalue())
        self.assertIn(IIFL_STALE, table.getvalue())  # noted row still shown with --only-issues
