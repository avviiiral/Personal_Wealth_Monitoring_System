import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import openpyxl

from market_data.services.security_master_generator import SecurityMasterGenerator


class SecurityMasterMergeTests(unittest.TestCase):
    def _write_workbook(self, path, rows):
        workbook = openpyxl.Workbook()
        worksheet = workbook.active
        worksheet.title = "Security Master"
        worksheet.append(SecurityMasterGenerator.OUTPUT_COLUMNS)
        for row in rows:
            worksheet.append([
                row.get(column)
                for column in SecurityMasterGenerator.OUTPUT_COLUMNS
            ])
        workbook.save(path)
        workbook.close()

    def test_refresh_preserves_existing_mapping_and_manual_nav(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "security_master.xlsx"
            existing = {
                "ISIN": "INE000A01021",
                "Security Name": "Example Ltd",
                "Yahoo Symbol": "EXAMPLE.NS",
                "Asset Type": "STOCK",
                "Manual NAV Enabled": False,
                "Manual NAV": None,
                "Active": True,
                "Resolution Status": "RESOLVED",
                "Price Source": "YAHOO_FINANCE",
                "Sector": "Industrials",
                "Cap Type": "Large Cap",
            }
            self._write_workbook(path, [existing])

            fresh = {
                "ISIN": "INE000A01021",
                "Security Name": "Example Limited",
                "Yahoo Symbol": "",
                "Asset Type": "STOCK",
                "Manual NAV Enabled": True,
                "Manual NAV": None,
                "Active": True,
                "Resolution Status": "UNRESOLVED",
                "Price Source": "",
                "Sector": "",
                "Cap Type": "",
            }

            with patch.object(
                SecurityMasterGenerator,
                "_output_file",
                return_value=path,
            ):
                merged = SecurityMasterGenerator._merge_existing_records([fresh])

            self.assertEqual(len(merged), 1)
            self.assertEqual(merged[0]["Yahoo Symbol"], "EXAMPLE.NS")
            self.assertEqual(merged[0]["Resolution Status"], "RESOLVED")
            self.assertEqual(merged[0]["Manual NAV Enabled"], False)
            self.assertIsNone(merged[0]["Manual NAV"])
            self.assertEqual(merged[0]["Sector"], "Industrials")
            self.assertEqual(merged[0]["Cap Type"], "Large Cap")

    def test_persist_resolved_symbol_updates_existing_isin_row(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "security_master.xlsx"
            self._write_workbook(path, [{
                "ISIN": "INE000A01021", "Security Name": "Example Ltd",
                "Yahoo Symbol": "", "Manual NAV Enabled": False,
                "Resolution Status": "UNRESOLVED", "Price Source": "",
            }])
            with patch.object(SecurityMasterGenerator, "_output_file", return_value=path), patch(
                "market_data.services.security_master_generator.SecurityResolver.reload_security_master"
            ):
                saved = SecurityMasterGenerator.persist_resolved_symbol(
                    isin="INE000A01021", security_name="Example Ltd", yahoo_symbol="EXAMPLE.NS"
                )
            workbook = openpyxl.load_workbook(path, data_only=True)
            ws = workbook["Security Master"]
            row = dict(zip([c.value for c in ws[1]], [c.value for c in ws[2]]))
            workbook.close()
            self.assertTrue(saved)
            self.assertEqual(row["Yahoo Symbol"], "EXAMPLE.NS")
            self.assertEqual(row["Resolution Status"], "RESOLVED")
            self.assertEqual(row["Price Source"], "YAHOO_FINANCE")
            self.assertFalse(row["Manual NAV Enabled"])

    def test_persist_resolved_symbol_refuses_to_replace_existing_mapping(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "security_master.xlsx"
            self._write_workbook(path, [{"ISIN": "INE000A01021", "Yahoo Symbol": "EXISTING.NS"}])
            with patch.object(SecurityMasterGenerator, "_output_file", return_value=path), patch(
                "market_data.services.security_master_generator.SecurityResolver.reload_security_master"
            ):
                with self.assertRaisesRegex(ValueError, "refusing to replace"):
                    SecurityMasterGenerator.persist_resolved_symbol(
                        isin="INE000A01021", security_name="Example Ltd", yahoo_symbol="OTHER.NS"
                    )

    def test_persist_resolved_symbol_creates_missing_master_row(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "security_master.xlsx"
            with patch.object(SecurityMasterGenerator, "_output_file", return_value=path), patch(
                "market_data.services.security_master_generator.SecurityResolver.reload_security_master"
            ):
                SecurityMasterGenerator.persist_resolved_symbol(
                    isin="INE999Z01011", security_name="New Example Ltd", yahoo_symbol="NEWEXAMPLE.NS"
                )
            workbook = openpyxl.load_workbook(path, data_only=True)
            ws = workbook["Security Master"]
            row = dict(zip([c.value for c in ws[1]], [c.value for c in ws[2]]))
            workbook.close()
            self.assertEqual(row["ISIN"], "INE999Z01011")
            self.assertEqual(row["Security Name"], "New Example Ltd")
            self.assertEqual(row["Yahoo Symbol"], "NEWEXAMPLE.NS")

    def test_refresh_retains_workbook_only_manual_records(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "security_master.xlsx"
            existing = {
                "ISIN": "IN0020210061",
                "Security Name": "Sovereign Gold Bond",
                "Asset Type": "BOND",
                "Manual NAV Enabled": True,
                "Active": True,
                "Resolution Status": "NON_YAHOO_ASSET",
                "Price Source": "MANUAL_OR_ASSET_SPECIFIC",
            }
            self._write_workbook(path, [existing])

            with patch.object(
                SecurityMasterGenerator,
                "_output_file",
                return_value=path,
            ):
                merged = SecurityMasterGenerator._merge_existing_records([])

            self.assertEqual(len(merged), 1)
            self.assertEqual(merged[0]["ISIN"], "IN0020210061")
            self.assertTrue(merged[0]["Manual NAV Enabled"])
            self.assertEqual(
                merged[0]["Price Source"],
                "MANUAL_OR_ASSET_SPECIFIC",
            )

    def test_refresh_adds_new_security_without_dropping_existing(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "security_master.xlsx"
            existing = {
                "ISIN": "INE000A01021",
                "Security Name": "Existing Ltd",
                "Yahoo Symbol": "EXISTING.NS",
                "Asset Type": "STOCK",
                "Active": True,
                "Resolution Status": "RESOLVED",
                "Price Source": "YAHOO_FINANCE",
            }
            self._write_workbook(path, [existing])

            fresh = {
                "ISIN": "INE999Z01011",
                "Security Name": "New Ltd",
                "Yahoo Symbol": "NEWCO.NS",
                "Asset Type": "STOCK",
                "Active": True,
                "Resolution Status": "RESOLVED",
                "Price Source": "YAHOO_FINANCE",
            }

            with patch.object(
                SecurityMasterGenerator,
                "_output_file",
                return_value=path,
            ):
                merged = SecurityMasterGenerator._merge_existing_records([fresh])

            by_isin = {row["ISIN"]: row for row in merged}
            self.assertEqual(set(by_isin), {"INE000A01021", "INE999Z01011"})
            self.assertEqual(by_isin["INE000A01021"]["Yahoo Symbol"], "EXISTING.NS")
            self.assertEqual(by_isin["INE999Z01011"]["Yahoo Symbol"], "NEWCO.NS")


if __name__ == "__main__":
    unittest.main()
