import hashlib
import json
import tempfile
from datetime import date
from decimal import Decimal
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import openpyxl
from django.contrib.auth.models import User
from django.core.management import call_command
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext

from investments.models import Asset, AssetCategory
from market_data.models import DataSource, MarketPrice
from market_data.services.security_master_generator import SecurityMasterGenerator
from market_data.services.security_resolver import SecurityResolver
from market_data.services.security_resolver_diagnostics import (
    STATUS_AMBIGUOUS,
    STATUS_CONFLICT,
    STATUS_MATCHED,
    STATUS_MISSING_ISIN,
    STATUS_NOT_IN_MASTER,
    STATUS_UNRESOLVED,
    diagnostic_assets,
    run_diagnostics,
)
from market_data.services.yahoo_finance import YahooFinanceService
from users.models import FamilyGroup

_RESOLVER_STATE = (
    "_security_master_loaded",
    "_security_master_isin",
    "_security_master_name",
    "_security_master_name_to_isin",
    "_security_master_isin_to_name",
    "_security_master_ambiguous_isin",
    "_security_master_ambiguous_name",
    "_security_master_ambiguous_name_to_isin",
    "_security_master_ambiguous_isin_to_name",
)

# Fictional identifiers: none of these exist in pwms_config.json mappings.
MATCHED_ISIN = "INE0DIAG0011"
DUPLICATE_ISIN = "INE0DIAG0022"
AMBIGUOUS_ISIN = "INE0DIAG0033"
CONFLICT_ISIN = "INE0DIAG0044"
NEW_ISIN = "INE0DIAG0055"


def _sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class SecurityResolverDiagnosticsTests(TestCase):
    def setUp(self):
        self._resolver_state = {
            name: getattr(SecurityResolver, name) for name in _RESOLVER_STATE
        }
        self.addCleanup(self._restore_resolver_state)

        # BASE_DIR/data/security_master.xlsx is the resolver's preferred
        # location; BASE_DIR.parent/data must not exist either.
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base_dir = Path(self._tmp.name) / "backend"
        (self.base_dir / "data").mkdir(parents=True)
        self.workbook = self.base_dir / "data" / "security_master.xlsx"
        settings_override = override_settings(BASE_DIR=self.base_dir, PWMS_DATA_DIR=str(self.base_dir / "data"))
        settings_override.enable()
        self.addCleanup(settings_override.disable)

        self.user = User.objects.create_user(username="diag_owner", password="x")
        self.family = FamilyGroup.objects.create(name="Diagnostics Family")

    def _restore_resolver_state(self):
        for name, value in self._resolver_state.items():
            setattr(SecurityResolver, name, value)

    def _write_workbook(self, rows):
        workbook = openpyxl.Workbook()
        worksheet = workbook.active
        worksheet.title = "Security Master"
        worksheet.append(SecurityMasterGenerator.OUTPUT_COLUMNS)
        for isin, name, symbol in rows:
            record = {column: None for column in SecurityMasterGenerator.OUTPUT_COLUMNS}
            record.update({"ISIN": isin, "Security Name": name, "Yahoo Symbol": symbol})
            worksheet.append([record[column] for column in SecurityMasterGenerator.OUTPUT_COLUMNS])
        workbook.save(self.workbook)
        workbook.close()

    def _asset(self, name, *, isin=None, symbol=None, category=AssetCategory.STOCK):
        return Asset.objects.create(
            owner=self.user, family=self.family, name=name,
            category=category, isin=isin, symbol=symbol,
        )

    def _standard_fixture(self):
        self._write_workbook([
            (MATCHED_ISIN, "Diag Matched Ltd", "DIAGMATCH.NS"),
            (DUPLICATE_ISIN, "Diag Duplicate Ltd", "DIAGDUP.NS"),
            (DUPLICATE_ISIN, "Diag Duplicate Ltd", "DIAGDUP.NS"),
            (AMBIGUOUS_ISIN, "Diag Ambiguous Ltd", "DIAGAMB1.NS"),
            (AMBIGUOUS_ISIN, "Diag Ambiguous Ltd", "DIAGAMB2.NS"),
            (CONFLICT_ISIN, "Diag Conflict Ltd", "DIAGMASTER.NS"),
        ])
        return {
            "matched": self._asset("Diag Matched Ltd", isin=MATCHED_ISIN, symbol="DIAGMATCH"),
            "matched_blank_symbol": self._asset("Diag Matched Ltd", isin=MATCHED_ISIN),
            "duplicate": self._asset("Diag Duplicate Ltd", isin=DUPLICATE_ISIN, symbol="DIAGDUP.NS"),
            "ambiguous": self._asset("Diag Ambiguous Ltd", isin=AMBIGUOUS_ISIN),
            "conflict": self._asset("Diag Conflict Ltd", isin=CONFLICT_ISIN, symbol="DIAGSTALE"),
            "not_in_master": self._asset("Diag New Ltd", isin=NEW_ISIN, symbol="DIAGNEW", category=AssetCategory.ETF),
            "missing_isin": self._asset("Diag No Isin Ltd", symbol="DIAGNOISIN"),
            "unresolved": self._asset("Diag Unmapped Security Zeta"),
        }

    def _rows_by_asset(self):
        SecurityResolver.reload_security_master(generate_if_missing=False)
        rows, workbook_error = run_diagnostics(diagnostic_assets(), workbook_path=self.workbook)
        self.assertIsNone(workbook_error)
        return {row.asset_id: row for row in rows}

    def test_statuses_cover_match_fallback_and_failure_cases(self):
        assets = self._standard_fixture()
        rows = self._rows_by_asset()

        matched = rows[assets["matched"].pk]
        self.assertEqual(matched.status, STATUS_MATCHED)
        self.assertEqual(matched.database_symbol, "DIAGMATCH")
        self.assertEqual(matched.security_master_symbol, "DIAGMATCH.NS")
        self.assertEqual(matched.resolved_symbol, "DIAGMATCH.NS")
        self.assertEqual(matched.reason, "")

        blank = rows[assets["matched_blank_symbol"].pk]
        self.assertEqual(blank.status, STATUS_MATCHED)
        self.assertIn("Asset.symbol is blank", blank.reason)

        duplicate = rows[assets["duplicate"].pk]
        self.assertEqual(duplicate.status, STATUS_MATCHED)
        self.assertEqual(duplicate.resolved_symbol, "DIAGDUP.NS")
        self.assertIn("2 identical Security Master rows", duplicate.reason)

        ambiguous = rows[assets["ambiguous"].pk]
        self.assertEqual(ambiguous.status, STATUS_AMBIGUOUS)
        self.assertEqual(ambiguous.resolved_symbol, "")
        self.assertEqual(ambiguous.security_master_symbol, "DIAGAMB1.NS | DIAGAMB2.NS")
        self.assertIn("conflicting Security Master mappings", ambiguous.reason)

        conflict = rows[assets["conflict"].pk]
        self.assertEqual(conflict.status, STATUS_CONFLICT)
        self.assertEqual(conflict.resolved_symbol, "")
        self.assertIn("Conflicting security identifiers", conflict.reason)
        self.assertIn("DIAGMASTER.NS", conflict.reason)
        self.assertIn("DIAGSTALE.NS", conflict.reason)

        not_in_master = rows[assets["not_in_master"].pk]
        self.assertEqual(not_in_master.status, STATUS_NOT_IN_MASTER)
        self.assertEqual(not_in_master.resolved_symbol, "DIAGNEW.NS")
        self.assertIn("ISIN not in Security Master", not_in_master.reason)

        missing = rows[assets["missing_isin"].pk]
        self.assertEqual(missing.status, STATUS_MISSING_ISIN)
        self.assertEqual(missing.isin, "")
        self.assertEqual(missing.resolved_symbol, "DIAGNOISIN.NS")

        unresolved = rows[assets["unresolved"].pk]
        self.assertEqual(unresolved.status, STATUS_UNRESOLVED)
        self.assertIn("Asset has no ISIN", unresolved.reason)
        self.assertIn("Unable to resolve security symbol", unresolved.reason)

    def test_default_scope_excludes_bonds_and_sgbs_but_explicit_ids_bypass_it(self):
        stock = self._asset("Diag Scope Stock", isin=MATCHED_ISIN)
        bond = self._asset("Diag Scope Bond", isin="INE0DIAG0066", category=AssetCategory.BOND)
        sgb = self._asset("Sovereign Gold Bond 2031", isin="IN0020DIAG77", category=AssetCategory.OTHER)
        mutual_fund = self._asset("Diag Scope Fund", isin="INF0DIAG0088", category=AssetCategory.MUTUAL_FUND)

        default_ids = set(diagnostic_assets().values_list("id", flat=True))
        self.assertIn(stock.pk, default_ids)
        self.assertNotIn(bond.pk, default_ids)
        self.assertNotIn(sgb.pk, default_ids)
        self.assertNotIn(mutual_fund.pk, default_ids)

        explicit = set(diagnostic_assets(asset_ids=[bond.pk], isins=["inf0diag0088"]).values_list("id", flat=True))
        self.assertEqual(explicit, {bond.pk, mutual_fund.pk})

    def test_command_is_read_only_for_database_workbook_and_providers(self):
        assets = self._standard_fixture()
        MarketPrice.objects.create(
            asset=assets["matched"], date=date(2026, 11, 20),
            close_price=Decimal("100"), source=DataSource.YAHOO_FINANCE,
        )
        before_hash = _sha256(self.workbook)
        before_mtime = self.workbook.stat().st_mtime_ns
        before_assets = list(Asset.objects.order_by("id").values())
        before_prices = list(MarketPrice.objects.order_by("id").values())

        forbidden = AssertionError("diagnostic must not write or fetch")
        output = StringIO()
        with patch.object(SecurityMasterGenerator, "persist_resolved_symbol", side_effect=forbidden), \
                patch.object(SecurityMasterGenerator, "generate", side_effect=forbidden), \
                patch.object(SecurityMasterGenerator, "_write_excel", side_effect=forbidden), \
                patch.object(YahooFinanceService, "fetch_history", side_effect=forbidden), \
                patch.object(YahooFinanceService, "save_history", side_effect=forbidden), \
                CaptureQueriesContext(connection) as queries:
            call_command("diagnose_security_resolver", stdout=output)

        writes = [
            query["sql"] for query in queries.captured_queries
            if query["sql"].lstrip().split(" ", 1)[0].upper() in {"INSERT", "UPDATE", "DELETE", "REPLACE", "CREATE", "DROP", "ALTER"}
        ]
        self.assertEqual(writes, [])
        self.assertEqual(_sha256(self.workbook), before_hash)
        self.assertEqual(self.workbook.stat().st_mtime_ns, before_mtime)
        self.assertEqual(list(Asset.objects.order_by("id").values()), before_assets)
        self.assertEqual(list(MarketPrice.objects.order_by("id").values()), before_prices)
        self.assertEqual(sorted(path.name for path in self.workbook.parent.iterdir()), ["security_master.xlsx"])

        text = output.getvalue()
        self.assertIn(f"Security Master: {self.workbook}", text)
        self.assertIn("Assets checked: 8", text)
        self.assertIn("MATCHED=3", text)
        self.assertIn("AMBIGUOUS=1", text)
        self.assertIn("CONFLICT=1", text)

    def test_missing_workbook_is_reported_not_generated(self):
        self._asset("Diag No Workbook Ltd", isin=NEW_ISIN, symbol="DIAGNEW")
        output = StringIO()
        with patch.object(
            SecurityMasterGenerator, "generate",
            side_effect=AssertionError("must not generate"),
        ):
            call_command("diagnose_security_resolver", "--format", "json", stdout=output)

        self.assertFalse(self.workbook.exists())
        self.assertEqual(list((self.base_dir / "data").iterdir()), [])
        payload = json.loads(output.getvalue())
        self.assertIsNone(payload["security_master"])
        self.assertIsNone(payload["security_master_error"])
        self.assertEqual(payload["assets_checked"], 1)
        self.assertEqual(payload["rows"][0]["status"], STATUS_NOT_IN_MASTER)
        self.assertEqual(payload["rows"][0]["resolved_symbol"], "DIAGNEW.NS")

    def test_unreadable_workbook_is_reported_not_raised(self):
        self.workbook.write_bytes(b"not an xlsx file")  # e.g. corrupt, or locked by Excel
        before_hash = _sha256(self.workbook)
        self._asset("Diag Unreadable Ltd", isin=NEW_ISIN, symbol="DIAGNEW")

        table = StringIO()
        call_command("diagnose_security_resolver", stdout=table)
        self.assertIn("could not be read", table.getvalue())
        self.assertIn("Assets checked: 1", table.getvalue())

        output = StringIO()
        call_command("diagnose_security_resolver", "--format", "json", stdout=output)
        payload = json.loads(output.getvalue())
        self.assertIn("BadZipFile", payload["security_master_error"])
        self.assertEqual(payload["rows"][0]["status"], STATUS_NOT_IN_MASTER)
        self.assertEqual(payload["rows"][0]["resolved_symbol"], "DIAGNEW.NS")
        self.assertEqual(_sha256(self.workbook), before_hash)

    def test_json_output_has_requested_fields_and_only_issues_filter(self):
        assets = self._standard_fixture()
        output = StringIO()
        call_command(
            "diagnose_security_resolver", "--format", "json", "--only-issues",
            stdout=output,
        )
        payload = json.loads(output.getvalue())
        self.assertEqual(payload["assets_checked"], 8)
        ids = {row["asset_id"] for row in payload["rows"]}
        self.assertNotIn(assets["matched"].pk, ids)  # clean MATCHED row hidden
        self.assertIn(assets["duplicate"].pk, ids)  # MATCHED with a note kept
        self.assertEqual(
            set(payload["rows"][0]),
            {
                "asset_id", "asset_name", "isin", "database_symbol",
                "security_master_symbol", "resolved_symbol", "status", "reason",
            },
        )


class SecurityMasterLoadGenerationFlagTests(TestCase):
    """A lookup never builds the workbook; the nightly security_master job does.

    (Previously a missing workbook was generated inline on the first price
    lookup, which blocked the price loop on NSE/Yahoo downloads.)
    """

    def setUp(self):
        state = {name: getattr(SecurityResolver, name) for name in _RESOLVER_STATE}
        self.addCleanup(lambda: [setattr(SecurityResolver, k, v) for k, v in state.items()])

    def test_default_load_does_not_generate_missing_workbook(self):
        with patch.object(SecurityResolver, "_security_master_path", return_value=None), \
                patch.object(SecurityResolver, "_generate_security_master", return_value=None) as generate:
            SecurityResolver.reload_security_master()
        generate.assert_not_called()

    def test_explicit_opt_in_still_generates_missing_workbook(self):
        with patch.object(SecurityResolver, "_security_master_path", return_value=None), \
                patch.object(SecurityResolver, "_generate_security_master", return_value=None) as generate:
            SecurityResolver.reload_security_master(generate_if_missing=True)
        generate.assert_called_once_with()

    def test_read_only_load_skips_generation(self):
        with patch.object(SecurityResolver, "_security_master_path", return_value=None), \
                patch.object(SecurityResolver, "_generate_security_master") as generate:
            SecurityResolver.reload_security_master(generate_if_missing=False)
        generate.assert_not_called()
        self.assertTrue(SecurityResolver._security_master_loaded)
        self.assertEqual(SecurityResolver._security_master_isin, {})
