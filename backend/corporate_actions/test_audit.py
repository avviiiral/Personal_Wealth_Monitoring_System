import json
import os
import tempfile
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase

from corporate_actions.audit import (
    extract_dividend_amount,
    extract_entitlement_ratio,
    inspect_isin_exposures,
    mark_duplicate_events,
)
from corporate_actions.models import (
    CorporateAction,
    CorporateActionExposure,
    CorporateActionNotification,
    CorporateActionReconciliation,
)
from investments.models import Asset, AssetCategory, AssetUnderlyingHolding, Holding
from mutual_funds.models import MutualFundHolding, MutualFundScheme
from mutual_funds.underlying_models import MutualFundUnderlying
from users.models import FamilyGroup
from django.contrib.auth.models import User


class CorporateActionAuditParsingTests(TestCase):
    def test_extracts_explicit_dividend_per_share(self):
        self.assertEqual(extract_dividend_amount("Dividend - Rs 4 Per Share"), Decimal("4"))
        self.assertEqual(extract_dividend_amount("Interim dividend of ₹ 2.50 each share"), Decimal("2.50"))
        self.assertIsNone(extract_dividend_amount("Dividend recommended"))

    def test_extracts_bonus_ratio_as_old_units_then_new_units(self):
        self.assertEqual(extract_entitlement_ratio("Bonus 1:2", "BONUS"), (2, 1))
        self.assertEqual(extract_entitlement_ratio("Bonus issue", "BONUS"), (None, None))

    def test_extracts_split_ratio_from_explicit_face_values(self):
        self.assertEqual(
            extract_entitlement_ratio("Face value split from Rs. 10/- to Rs. 1/-", "STOCK_SPLIT"),
            (Decimal("1"), Decimal("10")),
        )
        self.assertEqual(extract_entitlement_ratio("Stock split approved", "STOCK_SPLIT"), (None, None))

    def test_duplicate_detection_uses_isin_type_and_ex_date(self):
        rows = [
            {"isin": "INE000A01000", "action_type": "CASH_DIVIDEND", "ex_date": "2026-06-01",
             "validation_status": "VALID", "review_reasons": []},
            {"isin": "INE000A01000", "action_type": "CASH_DIVIDEND", "ex_date": "2026-06-01",
             "validation_status": "VALID", "review_reasons": []},
            {"isin": "INE000A01000", "action_type": "BONUS", "ex_date": "2026-06-01",
             "validation_status": "VALID", "review_reasons": []},
        ]
        mark_duplicate_events(rows)
        self.assertEqual(rows[1]["duplicate_of"], 0)
        self.assertIsNone(rows[2]["duplicate_of"])


class CorporateActionExposureAuditTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="audit-test", password="test-password")
        self.family = FamilyGroup.objects.create(name="Audit family")
        self.isin = "INE000A01000"

    def test_exact_isin_finds_direct_equity_mutual_fund_and_pms(self):
        direct = Asset.objects.create(
            owner=self.user, family=self.family, name="Direct Equity",
            category=AssetCategory.STOCK, isin=self.isin, symbol="AUDIT",
        )
        Holding.objects.create(owner=self.user, family=self.family, asset=direct, quantity=Decimal("12"))
        scheme = MutualFundScheme.objects.create(
            owner=self.user, family=self.family, scheme_name="Audit Mutual Fund",
        )
        MutualFundHolding.objects.create(
            owner=self.user, family=self.family, scheme=scheme, units=Decimal("5"),
        )
        MutualFundUnderlying.objects.create(
            scheme=scheme, security_name="Underlying Equity", isin=self.isin,
            security_key=self.isin, percentage_of_nav=Decimal("2.5"),
            portfolio_date=date(2026, 10, 31),
        )
        pms_asset = Asset.objects.create(
            owner=self.user, family=self.family, name="PMS Portfolio",
            category=AssetCategory.OTHER,
        )
        Holding.objects.create(owner=self.user, family=self.family, asset=pms_asset, quantity=Decimal("1"))
        AssetUnderlyingHolding.objects.create(
            owner=self.user, family=self.family, asset=pms_asset,
            stock_name="Underlying Equity", isin=self.isin,
            holding_percentage=Decimal("3.0"),
        )

        exposures, errors = inspect_isin_exposures(self.isin)
        self.assertEqual(errors, [])
        self.assertEqual(len(exposures["direct_equity"]), 1)
        self.assertEqual(len(exposures["mutual_fund"]), 1)
        self.assertEqual(len(exposures["pms"]), 1)
        self.assertEqual(inspect_isin_exposures("INE999Z99999")[0]["direct_equity"], [])

    @patch("corporate_actions.management.commands.monitor_corporate_actions.NseCorporateActionsProvider.fetch_symbol")
    def test_dry_run_json_report_does_not_mutate_database(self, fetch_symbol):
        asset = Asset.objects.create(
            owner=self.user, name="Audit Direct Equity", category=AssetCategory.STOCK,
            isin=self.isin, symbol="AUDIT",
        )
        Holding.objects.create(owner=self.user, asset=asset, quantity=Decimal("10"))
        fetch_symbol.return_value = [{
            "exchange": "NSE", "symbol": "AUDIT", "company_name": "Audit Limited",
            "purpose": "Dividend - Rs 4 Per Share", "isin": "",
            "ex_date": date(2026, 6, 1), "record_date": date(2026, 6, 2),
            "payment_date": None, "status": "", "source_url": "https://www.nseindia.com/companies-listing/corporate-filings-actions?symbol=AUDIT",
            "raw": {"symbol": "AUDIT", "subject": "Dividend - Rs 4 Per Share"},
        }]
        before = {
            "actions": CorporateAction.objects.count(),
            "exposures": CorporateActionExposure.objects.count(),
            "reconciliations": CorporateActionReconciliation.objects.count(),
            "notifications": CorporateActionNotification.objects.count(),
            "transactions": __import__("investments.models", fromlist=["Transaction"]).Transaction.objects.count(),
        }
        with tempfile.TemporaryDirectory() as directory:
            report_path = os.path.join(directory, "corporate_actions_audit.json")
            call_command(
                "monitor_corporate_actions", "--source", "nse", "--since", "2026-01-01",
                "--until", "2026-11-03", "--dry-run", "--report-json", report_path,
                verbosity=0,
            )
            with open(report_path, encoding="utf-8") as report_file:
                report = json.load(report_file)
        after = {
            "actions": CorporateAction.objects.count(),
            "exposures": CorporateActionExposure.objects.count(),
            "reconciliations": CorporateActionReconciliation.objects.count(),
            "notifications": CorporateActionNotification.objects.count(),
            "transactions": __import__("investments.models", fromlist=["Transaction"]).Transaction.objects.count(),
        }
        self.assertEqual(before, after)
        self.assertEqual(report["summary"]["audit_records"], 1)
        self.assertEqual(report["events"][0]["dividend_per_share"], "4")
        self.assertEqual(report["events"][0]["exposure_counts"]["direct_equity"], 1)
