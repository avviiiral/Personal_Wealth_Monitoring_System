"""reconcile_uploaded_corporate_actions: discovery only, read-only dry run, idempotent."""
import json
import os
import tempfile
from datetime import date
from decimal import Decimal
from io import StringIO
from unittest.mock import patch

import requests
from django.core.management import call_command
from django.test import TestCase

from investments.models import Asset, AssetCategory, Holding, Transaction
from users.models import FamilyGroup

from .models import (
    CorporateAction, CorporateActionExposure, CorporateActionLedgerEntry, CorporateActionNotification,
    CorporateActionPosition, CorporateActionReconciliation, CorporateActionStatus, CorporateActionType,
)
from .providers.nse import normalize_nse_action
from .reconciliation import reject_reconciliation
from .test_ledger_reconciliation import make_user

D = Decimal
ISIN = "INE000HIS011"
FETCH = "corporate_actions.providers.nse.NseCorporateActionsProvider.fetch_symbol"


def nse_row(purpose, ex_date, *, isin=ISIN, symbol="HIST"):
    row = {
        "symbol": symbol, "comp": "Historic Industries Limited", "series": "EQ", "subject": purpose,
        "faceVal": "10", "exDate": ex_date.strftime("%d-%b-%Y"), "recDate": ex_date.strftime("%d-%b-%Y"),
        "bcStartDate": "-", "bcEndDate": "-",
    }
    if isin is not None:
        row["isin"] = isin
    return row


def records(*rows):
    return [normalize_nse_action(row, "HIST") for row in rows]


SPLIT = "Face Value Split (Sub-Division) - From Rs 10/- Per Share To Rs 5/- Per Share"


class ReconcileUploadedCommandTests(TestCase):
    def setUp(self):
        self.user = make_user("ca-command-owner")
        self.family = FamilyGroup.objects.create(name="Command Family")
        self.user.profile.family_groups.add(self.family)
        self.asset = Asset.objects.create(
            owner=self.user, family=self.family, name="Historic Industries Ltd", category=AssetCategory.STOCK,
            symbol="HIST.NS", isin=ISIN, currency="INR", is_active=True,
        )
        # Bought in 2023, fully sold in 2025: not a current holding any more.
        self.buy = self.tx("BUY", date(2023, 3, 1), 100, 10000)
        self.tx("SELL", date(2025, 9, 1), 200, 26000)

    def tx(self, kind, tx_date, quantity, amount, **labels):
        values = {"family_name": "Member A", "portfolio": "Direct Equity", "asset_class": "Equity",
                  "sub_class": "Listed Equity", "asset_name": "Direct Equity", "underlying": "Historic Industries Ltd"}
        values.update(labels)
        return Transaction.objects.create(
            owner=self.user, family=self.family, asset=self.asset, transaction_type=kind,
            transaction_date=tx_date, quantity=D(str(quantity)), price_per_unit=D("0"), amount=D(str(amount)),
            fees=D("0"), source="EXCEL", **values,
        )

    def run_command(self, *args, rows=None, **kwargs):
        out = StringIO()
        with patch(FETCH, return_value=rows if rows is not None else []) as fetch:
            call_command("reconcile_uploaded_corporate_actions", *args, stdout=out, stderr=StringIO(), **kwargs)
        return out.getvalue(), fetch

    def snapshot(self):
        return tuple(model.objects.count() for model in (
            CorporateAction, CorporateActionReconciliation, CorporateActionPosition, CorporateActionNotification,
            CorporateActionExposure, CorporateActionLedgerEntry, Transaction, Holding,
        ))

    def test_dry_run_makes_no_database_changes(self):
        before = self.snapshot()
        output, _ = self.run_command("--dry-run", rows=records(nse_row(SPLIT, date(2024, 6, 14))))
        self.assertEqual(self.snapshot(), before)
        self.assertIn("dry_run=True", output)
        self.assertIn("eligible_positions=1", output)
        self.assertIn("pending_positions=1", output)
        self.assertIn("notifications_that_would_be_created=1", output)
        self.assertIn("new_notifications=0", output)

    def test_historical_securities_are_examined_and_reconciled_without_transactions(self):
        before = Transaction.objects.count()
        output, fetch = self.run_command(rows=records(nse_row(SPLIT, date(2024, 6, 14))))
        self.assertEqual(fetch.call_args.args[0], "HIST")
        self.assertEqual(fetch.call_args.args[1], date(2023, 3, 1))  # from the first uploaded transaction
        self.assertIn("securities_examined=1", output)
        reconciliation = CorporateActionReconciliation.objects.get(owner=self.user)
        self.assertEqual(reconciliation.status, "PENDING")
        self.assertEqual(reconciliation.action.isin, ISIN)
        self.assertEqual(reconciliation.action.metadata["identity_match_method"], "VERIFIED_PROVIDER_ISIN")
        position = CorporateActionPosition.objects.get(reconciliation=reconciliation)
        self.assertEqual((position.eligible_quantity, position.quantity_delta), (D("100"), D("100")))
        self.assertTrue(CorporateActionNotification.objects.filter(reconciliation=reconciliation).exists())
        self.assertEqual(Transaction.objects.count(), before)  # never an adjustment transaction

    def test_date_filters_limit_the_events(self):
        rows = records(nse_row(SPLIT, date(2024, 6, 14)))
        output, fetch = self.run_command("--since", "2025-01-01", "--until", "2025-12-31", rows=rows)
        self.assertEqual(fetch.call_args.args[1:], (date(2025, 1, 1), date(2025, 12, 31)))
        self.assertIn("corporate_actions_discovered=0", output)
        self.assertFalse(CorporateAction.objects.exists())

    def test_uploaded_corporate_action_rows_are_deduplicated(self):
        self.tx("SPLIT", date(2024, 6, 14), 100, 0)
        output, _ = self.run_command(rows=records(nse_row(SPLIT, date(2024, 6, 14))))
        self.assertIn("already_recorded=1", output)
        self.assertIn("pending_positions=0", output)
        self.assertEqual(CorporateActionReconciliation.objects.get(owner=self.user).status, "ALREADY_RECORDED")

    def test_repeated_runs_are_idempotent(self):
        rows = records(nse_row(SPLIT, date(2024, 6, 14)), nse_row("Bonus 1:1", date(2024, 10, 1)))
        self.run_command(rows=rows)
        first = self.snapshot()
        output, _ = self.run_command(rows=rows)
        self.assertEqual(self.snapshot(), first)
        self.assertIn("new_notifications=0", output)
        self.assertIn("events_created=0", output)
        self.assertEqual(CorporateActionReconciliation.objects.filter(owner=self.user).count(), 2)
        self.assertFalse(Transaction.objects.filter(source="OTHER").exists())

    def test_rejected_reconciliation_is_not_reopened(self):
        rows = records(nse_row(SPLIT, date(2024, 6, 14)))
        self.run_command(rows=rows)
        reconciliation = CorporateActionReconciliation.objects.get(owner=self.user)
        reject_reconciliation(owner=self.user, reconciliation_id=reconciliation.id, reason="Not mine")
        output, _ = self.run_command(rows=rows)
        reconciliation.refresh_from_db()
        self.assertEqual(reconciliation.status, "REJECTED")
        self.assertIn("terminal_unchanged=1", output)

    def test_conflicting_provider_isin_is_reported_as_ambiguous(self):
        output, _ = self.run_command(rows=records(nse_row(SPLIT, date(2024, 6, 14), isin="INE999ZZZ011")))
        self.assertIn("ambiguous_identities=1", output)
        self.assertFalse(CorporateAction.objects.exists())

    def test_missing_provider_isin_requires_identity_approval(self):
        self.asset.symbol = "HIST"
        self.asset.save(update_fields=["symbol"])
        output, _ = self.run_command(rows=records(nse_row(SPLIT, date(2024, 6, 14), isin=None)))
        self.assertIn("ambiguous_identities=1", output)
        reconciliation = CorporateActionReconciliation.objects.get(owner=self.user)
        self.assertEqual(reconciliation.status, "PENDING_USER_APPROVAL")
        self.assertEqual(reconciliation.candidate_asset_ids, [self.asset.id])

    def test_unclassified_or_incomplete_events_are_reported(self):
        output, _ = self.run_command(rows=records(nse_row("Annual General Meeting", date(2024, 6, 14)),
                                                  nse_row("Bonus", date(2024, 7, 1))))
        self.assertIn("insufficient_data=1", output)
        # The bonus without an explicit ratio is recorded for review, never guessed.
        reconciliation = CorporateActionReconciliation.objects.get(owner=self.user)
        self.assertEqual(reconciliation.status, "MANUAL_REVIEW")
        self.assertIn("manual_review=1", output)

    def test_stored_events_are_reused_and_copied_to_exposed_family_members(self):
        member = make_user("ca-command-member")
        member.profile.family_groups.add(self.family)
        CorporateAction.objects.create(
            owner=member, action_type=CorporateActionType.BONUS, security_name="Historic Industries",
            isin=ISIN, ex_date=date(2024, 8, 1), old_units=D("1"), new_units=D("1"),
            status=CorporateActionStatus.CONFIRMED, source="MANUAL",
        )
        output, fetch = self.run_command("--source", "db")
        fetch.assert_not_called()
        self.assertIn("corporate_actions_discovered=1", output)
        mine = CorporateAction.objects.get(owner=self.user)
        self.assertEqual((mine.old_units, mine.new_units), (D("1"), D("1")))
        self.assertEqual(mine.status, CorporateActionStatus.PENDING)  # my own review is still required
        for owner in (self.user, member):
            self.assertEqual(CorporateActionReconciliation.objects.get(owner=owner).status, "PENDING")

    def test_owner_family_and_asset_filters(self):
        other_family = FamilyGroup.objects.create(name="Other command family")
        other_user = make_user("ca-command-other")
        other_user.profile.family_groups.add(other_family)
        other_asset = Asset.objects.create(owner=other_user, family=other_family, name="Other Co",
                                           category=AssetCategory.STOCK, symbol="OTHR.NS", isin="INE000OTH011")
        Transaction.objects.create(owner=other_user, family=other_family, asset=other_asset, transaction_type="BUY",
                                   transaction_date=date(2023, 1, 1), quantity=D("5"), price_per_unit=D("1"),
                                   amount=D("5"), family_name="X", portfolio="X", asset_class="Equity",
                                   sub_class="Listed", asset_name="X", underlying="Other Co")
        output, fetch = self.run_command("--family-id", str(self.family.id), "--dry-run")
        self.assertIn("securities_examined=1", output)
        self.assertEqual([call.args[0] for call in fetch.call_args_list], ["HIST"])
        output, fetch = self.run_command("--asset-id", str(other_asset.id), "--dry-run")
        self.assertEqual([call.args[0] for call in fetch.call_args_list], ["OTHR"])
        output, _ = self.run_command("--owner-id", str(other_user.id), "--dry-run")
        self.assertIn("securities_examined=1", output)

    def test_report_json_and_fetch_errors(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "report.json")
            out = StringIO()
            with patch(FETCH, side_effect=requests.ConnectionError("blocked")):
                call_command("reconcile_uploaded_corporate_actions", "--dry-run", "--report-json", path,
                             stdout=out, stderr=StringIO())
            with open(path, encoding="utf-8") as handle:
                report = json.load(handle)
        self.assertTrue(report["dry_run"])
        self.assertEqual(report["summary"]["errors"], 1)
        self.assertIn("blocked", report["errors"][0])
        self.assertIn("errors=1", out.getvalue())

    def test_one_malformed_provider_record_is_reported_and_the_rest_continue(self):
        good, bad = records(nse_row(SPLIT, date(2024, 6, 14)), nse_row("Bonus 1:1", date(2024, 10, 1)))
        bad["ex_date"] = "not-a-date"  # a provider record that cannot be read
        output, _ = self.run_command(rows=[bad, good])
        self.assertIn("errors=1", output)
        self.assertIn("corporate_actions_discovered=1", output)
        self.assertEqual(CorporateActionReconciliation.objects.get(owner=self.user).status, "PENDING")
        self.assertFalse(Transaction.objects.filter(source="OTHER").exists())

    def test_database_failure_for_one_event_rolls_back_only_that_event(self):
        from django.db import IntegrityError

        from .services import upsert_corporate_action as real_upsert

        def flaky_upsert(*, owner, lookup, defaults):
            if lookup["action_type"] == CorporateActionType.STOCK_SPLIT:
                real_upsert(owner=owner, lookup=lookup, defaults=defaults)  # a write that must be rolled back
                raise IntegrityError("simulated database failure")
            return real_upsert(owner=owner, lookup=lookup, defaults=defaults)

        rows = records(nse_row(SPLIT, date(2024, 6, 14)), nse_row("Bonus 1:1", date(2024, 10, 1)))
        with patch("corporate_actions.management.commands.reconcile_uploaded_corporate_actions.upsert_corporate_action",
                   side_effect=flaky_upsert):
            output, _ = self.run_command(rows=rows)
        self.assertIn("errors=1", output)
        self.assertEqual(list(CorporateAction.objects.values_list("action_type", flat=True)), [CorporateActionType.BONUS])
        self.assertEqual(CorporateActionReconciliation.objects.get(owner=self.user).action.action_type,
                         CorporateActionType.BONUS)
        self.assertFalse(Transaction.objects.filter(source="OTHER").exists())

    def test_unexpected_provider_failure_is_counted_and_the_run_completes(self):
        out = StringIO()
        with patch(FETCH, side_effect=RuntimeError("provider bug")):
            call_command("reconcile_uploaded_corporate_actions", "--source", "nse", stdout=out, stderr=StringIO())
        self.assertIn("errors=1", out.getvalue())
        self.assertFalse(CorporateAction.objects.exists())

    def test_ledger_read_failure_is_a_clear_command_error_and_writes_nothing(self):
        from django.core.management.base import CommandError
        from django.db import OperationalError

        before = self.snapshot()
        with patch("corporate_actions.management.commands.reconcile_uploaded_corporate_actions.Command._transactions",
                   side_effect=OperationalError("database is locked")):
            with self.assertRaisesMessage(CommandError, "No transaction or holding was changed"):
                call_command("reconcile_uploaded_corporate_actions", stdout=StringIO(), stderr=StringIO())
        self.assertEqual(self.snapshot(), before)

    def test_unwritable_report_path_is_a_clear_command_error(self):
        from django.core.management.base import CommandError

        with patch(FETCH, return_value=[]):
            with self.assertRaises(CommandError):
                call_command("reconcile_uploaded_corporate_actions", "--dry-run",
                             "--report-json", os.path.join(tempfile.gettempdir(), "missing-dir", "x", "r.json"),
                             stdout=StringIO(), stderr=StringIO())

    def test_dry_run_reports_the_same_proposal_a_real_run_stores(self):
        rows = records(nse_row("Dividend - Rs 2.50 Per Share", date(2024, 7, 5)))
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "report.json")
            self.run_command("--dry-run", "--report-json", path, rows=rows)
            with open(path, encoding="utf-8") as handle:
                planned = json.load(handle)["reconciliations"][0]
        self.assertEqual(planned["status"], "PENDING")
        self.assertEqual(D(planned["positions"][0]["eligible_quantity"]), D("100"))
        self.run_command(rows=rows)
        reconciliation = CorporateActionReconciliation.objects.get(owner=self.user)
        self.assertEqual(reconciliation.status, planned["status"])
        self.assertEqual(reconciliation.action.cash_per_unit, D("2.5"))
        self.assertEqual(reconciliation.action.ex_date, date(2024, 7, 5))
