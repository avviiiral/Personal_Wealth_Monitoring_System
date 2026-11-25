"""NSE corporate-action identity matching.

Default (corporate_actions.nse_identity_matching=false): behaviour is exactly
as before. That is pinned by the "current behaviour" tests, which show why
NSE events never reconciled against holdings stored with a ".NS" symbol.

With the flag on: the provider's own ISIN is used; otherwise an NSE symbol is
mapped to an ISIN only when the Security Master or EQUITY_L.csv gives exactly
one answer. The match method is recorded, the review/approval flow is
unchanged, and no transaction is ever written by a scan.
"""
from datetime import date, timedelta
from decimal import Decimal
from io import StringIO
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from corporate_actions.identity import resolve_nse_symbol_isin
from corporate_actions.models import CorporateAction, CorporateActionReconciliation
from corporate_actions.providers.nse import normalize_nse_action
from investments.models import Asset, AssetCategory, Holding, Transaction
from users.models import FamilyGroup

HDFC_ISIN = "INE040A01034"
HELD_QUANTITY = Decimal("10")
FLAG_ON = {"corporate_actions": {"nse_identity_matching": True}}


def nse_row(*, isin=HDFC_ISIN, symbol="HDFCBANK", ex_date=None):
    ex_date = ex_date or (timezone.localdate() - timedelta(days=10))
    row = {
        "symbol": symbol, "comp": "HDFC Bank Limited", "series": "EQ",
        "subject": "Dividend - Rs 22 Per Share", "faceVal": "1",
        "exDate": ex_date.strftime("%d-%b-%Y"), "recDate": ex_date.strftime("%d-%b-%Y"),
        "bcStartDate": "-", "bcEndDate": "-",
    }
    if isin is not None:
        row["isin"] = isin
    return row


class ProviderNormalizationTests(SimpleTestCase):
    def test_provider_isin_is_always_kept_for_the_audit(self):
        self.assertEqual(normalize_nse_action(nse_row(), "HDFCBANK")["provider_isin"], HDFC_ISIN)
        self.assertEqual(normalize_nse_action(nse_row(isin=None), "HDFCBANK")["provider_isin"], "")

    def test_isin_is_used_for_matching_only_with_the_flag_on(self):
        self.assertEqual(normalize_nse_action(nse_row(), "HDFCBANK")["isin"], "")
        with patch("corporate_actions.identity.get_pwms_config", return_value=True):
            self.assertEqual(normalize_nse_action(nse_row(), "HDFCBANK")["isin"], HDFC_ISIN)


class SymbolToIsinTests(SimpleTestCase):
    def test_unique_security_master_symbol_resolves(self):
        with patch("corporate_actions.identity._security_master_isins_for_symbol", return_value={HDFC_ISIN}), \
                patch("corporate_actions.identity._equity_list_isins_for_symbol", return_value=set()):
            self.assertEqual(resolve_nse_symbol_isin("HDFCBANK"), (HDFC_ISIN, "SYMBOL_SECURITY_MASTER"))

    def test_equity_list_is_used_when_security_master_has_no_answer(self):
        with patch("corporate_actions.identity._security_master_isins_for_symbol", return_value=set()), \
                patch("corporate_actions.identity._equity_list_isins_for_symbol", return_value={HDFC_ISIN}):
            self.assertEqual(resolve_nse_symbol_isin("HDFCBANK"), (HDFC_ISIN, "SYMBOL_EQUITY_L"))

    def test_ambiguous_or_conflicting_answers_never_match(self):
        with patch("corporate_actions.identity._security_master_isins_for_symbol", return_value={HDFC_ISIN, "INE000X01011"}), \
                patch("corporate_actions.identity._equity_list_isins_for_symbol", return_value=set()):
            self.assertEqual(resolve_nse_symbol_isin("HDFCBANK"), (None, "AMBIGUOUS"))
        with patch("corporate_actions.identity._security_master_isins_for_symbol", return_value={HDFC_ISIN}), \
                patch("corporate_actions.identity._equity_list_isins_for_symbol", return_value={"INE000X01011"}):
            self.assertEqual(resolve_nse_symbol_isin("HDFCBANK"), (None, "AMBIGUOUS"))
        with patch("corporate_actions.identity._security_master_isins_for_symbol", return_value=set()), \
                patch("corporate_actions.identity._equity_list_isins_for_symbol", return_value=set()):
            self.assertEqual(resolve_nse_symbol_isin("HDFCBANK"), (None, "NOT_FOUND"))


class NseScanMatchingTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="corp-nse-owner", password="pw")
        self.family = FamilyGroup.objects.create(name="Corp NSE Family")
        self.user.profile.family_groups.add(self.family)
        # As stored in production: resolve_asset_symbol saves the Yahoo symbol.
        self.asset = Asset.objects.create(
            owner=self.user, family=self.family, name="HDFC Bank Ltd", category=AssetCategory.STOCK,
            symbol="HDFCBANK.NS", isin=HDFC_ISIN, currency="INR", is_active=True,
        )
        Transaction.objects.create(
            owner=self.user, family=self.family, family_name="M", portfolio="P", asset_class="Equity",
            sub_class="Direct Equity", asset_name=self.asset.name, underlying="", asset=self.asset,
            transaction_type="BUY", transaction_date=date(2024, 1, 5), quantity=HELD_QUANTITY,
            price_per_unit=Decimal("1500"), amount=Decimal("15000"), fees=Decimal("0"),
        )
        Holding.objects.create(owner=self.user, family=self.family, asset=self.asset, quantity=HELD_QUANTITY)
        self.transactions_before = Transaction.objects.count()

    def _scan(self, rows, overrides=None):
        from config import pwms_config

        config = {key: dict(value) if isinstance(value, dict) else value for key, value in pwms_config.CONFIG.items()}
        for section, values in (overrides or {}).items():
            config[section] = {**config.get(section, {}), **values}
        with patch.dict(pwms_config.CONFIG, config):
            records = [normalize_nse_action(row, "HDFCBANK") for row in rows]
            with patch("corporate_actions.providers.nse.NseCorporateActionsProvider.fetch_symbol", return_value=records):
                call_command("monitor_corporate_actions", source="nse", stdout=StringIO())

    def test_current_behaviour_provider_isin_is_dropped_and_nothing_reconciles(self):
        # The event is only stored per matched owner, so an unmatched NSE
        # event leaves no row at all: it is logged and forgotten.
        self._scan([nse_row()])
        self.assertFalse(CorporateAction.objects.exists())
        self.assertFalse(CorporateActionReconciliation.objects.exists())

    def test_current_behaviour_symbol_suffix_prevents_the_candidate_match(self):
        self._scan([nse_row(isin=None)])
        self.assertFalse(CorporateActionReconciliation.objects.exists())

    def test_flag_on_uses_the_provider_isin(self):
        self._scan([nse_row()], FLAG_ON)
        action = CorporateAction.objects.get()
        self.assertEqual(action.isin, HDFC_ISIN)
        self.assertEqual(action.metadata["identity_match_method"], "SOURCE_ISIN")
        reconciliation = CorporateActionReconciliation.objects.get(action=action, owner=self.user)
        self.assertEqual(reconciliation.identity_match_method, "SOURCE_ISIN")
        self.assertEqual(reconciliation.status, "PENDING")
        self.assertEqual(reconciliation.suggested_quantity, HELD_QUANTITY)
        self.assertEqual(Transaction.objects.count(), self.transactions_before)

    def test_flag_on_maps_a_unique_symbol_and_records_the_method(self):
        with patch("corporate_actions.identity._security_master_isins_for_symbol", return_value={HDFC_ISIN}), \
                patch("corporate_actions.identity._equity_list_isins_for_symbol", return_value=set()):
            self._scan([nse_row(isin=None)], FLAG_ON)
        action = CorporateAction.objects.get()
        self.assertEqual(action.isin, HDFC_ISIN)
        self.assertIsNone(action.metadata["source_isin"])
        self.assertEqual(action.metadata["identity_match_method"], "SYMBOL_SECURITY_MASTER")
        reconciliation = CorporateActionReconciliation.objects.get(action=action, owner=self.user)
        self.assertEqual(reconciliation.identity_match_method, "SYMBOL_SECURITY_MASTER")
        self.assertEqual(Transaction.objects.count(), self.transactions_before)

    def test_flag_on_ambiguous_symbol_falls_back_to_explicit_approval(self):
        with patch("corporate_actions.identity._security_master_isins_for_symbol", return_value={HDFC_ISIN, "INE000X01011"}), \
                patch("corporate_actions.identity._equity_list_isins_for_symbol", return_value=set()):
            self._scan([nse_row(isin=None)], FLAG_ON)
        action = CorporateAction.objects.get()
        self.assertEqual(action.isin, "")
        reconciliation = CorporateActionReconciliation.objects.get(action=action, owner=self.user)
        # The ".NS" suffix no longer hides the candidate; approval is still required.
        self.assertEqual(reconciliation.status, "PENDING_USER_APPROVAL")
        self.assertEqual(reconciliation.identity_match_method, "SYMBOL")
        self.assertEqual(reconciliation.candidate_asset_ids, [self.asset.id])
        self.assertEqual(Transaction.objects.count(), self.transactions_before)

    def test_audit_command_reports_why_events_did_not_match_and_changes_nothing(self):
        rows = [nse_row(), nse_row(isin=None, ex_date=timezone.localdate() - timedelta(days=40))]
        counts_before = (CorporateAction.objects.count(), CorporateActionReconciliation.objects.count(),
                         Transaction.objects.count())
        out = StringIO()
        with patch("corporate_actions.identity._security_master_isins_for_symbol", return_value={HDFC_ISIN}), \
                patch("corporate_actions.identity._equity_list_isins_for_symbol", return_value=set()), \
                patch("corporate_actions.providers.nse.NseCorporateActionsProvider.fetch_symbol",
                      return_value=[normalize_nse_action(row, "HDFCBANK") for row in rows]):
            call_command("audit_corporate_action_matching", stdout=out)
        report = out.getvalue()
        self.assertIn("NSE events: 2", report)
        self.assertIn("matched holdings now: 0", report)
        self.assertIn("provider ISIN dropped: 1", report)
        self.assertIn("symbol stored with .NS suffix", report)
        self.assertIn("would match with nse_identity_matching=true: 2", report)
        self.assertEqual(counts_before, (CorporateAction.objects.count(), CorporateActionReconciliation.objects.count(),
                                         Transaction.objects.count()))


    def test_dry_run_with_an_isin_less_nse_event_does_not_crash(self):
        # Previously raised UnboundLocalError (a local "import Q" shadowed the
        # module-level one), so --dry-run failed on every real NSE event.
        records = [normalize_nse_action(nse_row(isin=None), "HDFCBANK")]
        with patch("corporate_actions.providers.nse.NseCorporateActionsProvider.fetch_symbol", return_value=records):
            out = StringIO()
            call_command("monitor_corporate_actions", source="nse", dry_run=True, stdout=out)
        self.assertIn("dry_run=True", out.getvalue())
        self.assertFalse(CorporateAction.objects.exists())
