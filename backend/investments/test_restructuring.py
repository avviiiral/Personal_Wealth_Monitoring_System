"""Mergers and demergers in transaction uploads.

An upload row typed *Merger / *Merged / *DeMerger / *DeMerger (New) is a
corporate action, not a transaction. The importer must hold it for review
(nothing imported, nothing created) and only the explicit
`review_restructurings --approve` command may create transactions, carrying
cost without changing P&L, XIRR or any calculation.
"""
import io
from datetime import date
from decimal import Decimal
from io import StringIO
from unittest.mock import patch

import openpyxl
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import CommandError, call_command
from django.test import SimpleTestCase, TestCase

from investments.models import Asset, PendingRestructuring, Transaction, TransactionUpload
from investments.services import restructuring
from investments.services.transaction_import import TransactionImporter
from portfolio.services.holding_engine import HoldingCalculationEngine
from users.models import FamilyGroup

PMS = "ABAKKUS Mprofit"
HEADER = [
    "Family Name", "Asset Class", "Sub Class", "Asset Name", "Underlying", "Advisors", "ISIN",
    "Date", "Trans. Type", "Quantity", "Price", "Amount",
]


def upload_file(rows):
    """rows: (underlying, date_text, type, quantity, price, amount[, pms_name])"""
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "Transactions"
    sheet.append(HEADER)
    for underlying, when, kind, quantity, price, amount, *rest in rows:
        sheet.append([
            "DAJ", "Equity", "Equity PMS", rest[0] if rest else PMS, underlying, "Direct", "",
            when, kind, quantity, price, amount,
        ])
    buffer = io.BytesIO()
    workbook.save(buffer)
    return SimpleUploadedFile("t.xlsx", buffer.getvalue(), content_type="application/octet-stream")


class RestructuringTypeTests(SimpleTestCase):
    def test_all_four_spellings_are_recognised_case_insensitively(self):
        self.assertEqual(restructuring.kind_of("*MERGER"), ("MERGER", "TO"))
        self.assertEqual(restructuring.kind_of("*Merged"), ("MERGER", "FROM"))
        self.assertEqual(restructuring.kind_of("*DEMERGER"), ("DEMERGER", "PARENT"))
        self.assertEqual(restructuring.kind_of("*DeMerger (New)"), ("DEMERGER", "CHILD"))
        self.assertEqual(restructuring.kind_of("*DEMERGER(NEW)"), ("DEMERGER", "CHILD"))

    def test_other_types_are_not_restructurings(self):
        for value in ("BUY", "*SPLIT", "*FOO", "", None, "MERGER"):
            self.assertIsNone(restructuring.kind_of(value), value)


class RestructuringTestBase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="restructure", password="x")
        self.family = FamilyGroup.objects.create(name="Restructure Family")
        self.user.profile.family_groups.add(self.family)
        # No network: the importer's security enrichment is not under test.
        for target in ("SecurityResolver.resolve_yahoo_symbol", "enrich_quant_fields"):
            patcher = patch(f"investments.services.transaction_import.{target}", return_value=None)
            patcher.start()
            self.addCleanup(patcher.stop)

    def import_rows(self, rows):
        batch = TransactionUpload.objects.create(owner=self.user, family=self.family, file_name="t.xlsx")
        return TransactionImporter.import_file(file=upload_file(rows), owner=self.user, upload_batch=batch)

    def asset(self, name):
        return Asset.objects.get(family=self.family, name=name)

    def position(self, name):
        return HoldingCalculationEngine.calculate_position(self.asset(name))

    def run_command(self, *args):
        out = StringIO()
        call_command("review_restructurings", *args, stdout=out)
        return out.getvalue()

    def transaction_count(self):
        return Transaction.objects.filter(family=self.family).count()


MERGER_ROWS = [
    ("Jindal Stainless (Hisar)", "01-05-2022", "Buy", "4520", "100", "452000"),
    ("Jindal Stainless (Hisar)", "09-03-2023", "*Merged", "4,520", "203.12", "918,090.51"),
    ("Jindal Stainless", "09-03-2023", "*Merger", "8,814", "104.16", "918,090.51"),
]
DEMERGER_ROWS = [
    ("Allcargo Logistics", "01-05-2022", "Buy", "5863", "100", "586300"),
    ("Allcargo Logistics", "18-04-2023", "*DeMerger", "5,863", "110.58", "648,313.90"),
    ("Allcargo Terminals", "18-04-2023", "*DeMerger (New)", "5,863", "18.42", "107,981.28"),
    ("Transindia Real Estate", "18-04-2023", "*DeMerger (New)", "5,863", "113.34", "664,511.09"),
]


class HoldingRowsTests(RestructuringTestBase):
    def test_merger_rows_are_held_not_imported_and_create_nothing(self):
        result = self.import_rows(MERGER_ROWS)
        self.assertEqual(result["total_imported"], 1)  # only the BUY
        self.assertEqual(result["failed_rows"], 2)
        for failure in result["failures"]:
            self.assertIn("Held for review", failure["message"])
            self.assertIn("review_restructurings", failure["message"])
        self.assertEqual(self.transaction_count(), 1)
        self.assertFalse(Asset.objects.filter(family=self.family, name="Jindal Stainless").exists())
        pending = PendingRestructuring.objects.get()
        self.assertEqual((pending.kind, pending.status, pending.event_date), ("MERGER", "PENDING", date(2023, 3, 9)))
        self.assertEqual(sorted(leg["role"] for leg in pending.legs), ["FROM", "TO"])
        self.assertEqual(pending.asset_name, PMS)

    def test_upload_counts_keep_their_shape(self):
        batch = TransactionUpload.objects.create(owner=self.user, family=self.family, file_name="t.xlsx")
        TransactionImporter.import_file(file=upload_file(MERGER_ROWS), owner=self.user, upload_batch=batch)
        batch.refresh_from_db()
        self.assertEqual((batch.total_rows, batch.imported_rows, batch.failed_rows), (3, 1, 2))
        self.assertEqual(batch.status, "PARTIAL")
        self.assertEqual(batch.failures.count(), 2)

    def test_uploading_the_same_file_again_does_not_duplicate_the_review_item(self):
        self.import_rows(MERGER_ROWS)
        self.import_rows(MERGER_ROWS)
        self.assertEqual(PendingRestructuring.objects.count(), 1)

    def test_two_pairs_on_one_date_stay_separate_reviews(self):
        rows = MERGER_ROWS + [
            ("IDFC Limited", "09-03-2023", "*Merged", "35,085", "54.4782", "1,911,341.46"),
            ("IDFC First Bank", "09-03-2023", "*Merger", "54,381", "35.15", "1,911,341.46"),
        ]
        self.import_rows(rows)
        self.assertEqual(PendingRestructuring.objects.count(), 2)

    def test_demerger_group_has_parent_and_children(self):
        result = self.import_rows(DEMERGER_ROWS)
        self.assertEqual(result["failed_rows"], 3)
        pending = PendingRestructuring.objects.get()
        self.assertEqual(pending.kind, "DEMERGER")
        self.assertEqual(sorted(leg["role"] for leg in pending.legs), ["CHILD", "CHILD", "PARENT"])

    def test_unknown_star_types_still_fail_as_unsupported(self):
        result = self.import_rows([("Jindal Stainless", "09-03-2023", "*Foo", "1", "1", "1")])
        self.assertIn("Unsupported investment transaction type: *FOO", result["failures"][0]["message"])
        self.assertEqual(PendingRestructuring.objects.count(), 0)


class MergerReviewTests(RestructuringTestBase):
    def setUp(self):
        super().setUp()
        self.import_rows(MERGER_ROWS)
        self.pending = PendingRestructuring.objects.get()

    def test_listing_is_read_only_and_shows_the_cost_to_carry(self):
        before = self.transaction_count()
        report = self.run_command()
        self.assertEqual(self.transaction_count(), before)
        self.assertIn(f"#{self.pending.id}", report)
        self.assertIn("MERGER", report)
        self.assertIn("452000.0000", report)   # the cost the old stock holds today
        self.assertIn("--approve", report)
        self.pending.refresh_from_db()
        self.assertEqual(self.pending.status, "PENDING")

    def test_approve_without_cost_only_previews(self):
        before = self.transaction_count()
        report = self.run_command("--approve", str(self.pending.id))
        self.assertEqual(self.transaction_count(), before)
        self.assertIn("--cost 452000.0000", report)

    def test_wrong_cost_is_refused_and_creates_nothing(self):
        before = self.transaction_count()
        with self.assertRaisesRegex(CommandError, "does not match"):
            self.run_command("--approve", str(self.pending.id), "--cost", "918090.51")
        self.assertEqual(self.transaction_count(), before)
        self.pending.refresh_from_db()
        self.assertEqual(self.pending.status, "PENDING")

    def test_approval_carries_cost_with_no_realized_gain_and_net_zero_cash(self):
        self.run_command("--approve", str(self.pending.id), "--cost", "452000")
        old, new = self.position("Jindal Stainless (Hisar)"), self.position("Jindal Stainless")
        self.assertEqual((old["quantity"], old["invested_value"]), (Decimal("0"), Decimal("0")))
        self.assertEqual((new["quantity"], new["invested_value"]), (Decimal("8814"), Decimal("452000.0000")))
        created = list(Transaction.objects.filter(source="OTHER", family=self.family).order_by("transaction_type"))
        self.assertEqual([(t.transaction_type, t.transaction_date) for t in created],
                         [("BUY", date(2023, 3, 9)), ("SELL", date(2023, 3, 9))])
        buy, sell = created
        self.assertEqual(buy.amount, sell.amount)   # same-day, equal and opposite: XIRR unchanged
        self.assertEqual(sell.amount, Decimal("452000.0000"))  # sold at its own cost: no realized gain
        self.assertEqual((buy.asset_name, buy.family_name, buy.underlying), (PMS, "DAJ", "Jindal Stainless"))
        self.pending.refresh_from_db()
        self.assertEqual((self.pending.status, len(self.pending.applied_transaction_ids)), ("APPLIED", 2))

    def test_approving_twice_creates_nothing_more(self):
        self.run_command("--approve", str(self.pending.id), "--cost", "452000")
        count = self.transaction_count()
        with self.assertRaisesRegex(CommandError, "already"):
            self.run_command("--approve", str(self.pending.id), "--cost", "452000")
        self.assertEqual(self.transaction_count(), count)

    def test_reject_changes_no_transactions(self):
        before = self.transaction_count()
        self.run_command("--reject", str(self.pending.id))
        self.pending.refresh_from_db()
        self.assertEqual(self.pending.status, "REJECTED")
        self.assertEqual(self.transaction_count(), before)

    def test_shared_holding_needs_an_explicit_cost(self):
        # The old stock is also held in another PMS, so its cost for THIS PMS is not derivable.
        self.import_rows([("Jindal Stainless (Hisar)", "02-05-2022", "Buy", "100", "100", "10000", "Other PMS")])
        report = self.run_command()
        self.assertIn("not available", report)
        count = self.transaction_count()
        with self.assertRaisesRegex(CommandError, "--cost"):
            self.run_command("--approve", str(self.pending.id))
        self.run_command("--approve", str(self.pending.id), "--cost", "452000")
        self.assertEqual(self.transaction_count(), count + 2)

    def test_quantity_mismatch_is_refused_unless_forced(self):
        Transaction.objects.filter(family=self.family, transaction_type="BUY").update(quantity=Decimal("4000"))
        with self.assertRaisesRegex(CommandError, "quantity"):
            self.run_command("--approve", str(self.pending.id), "--cost", "452000")
        self.run_command("--approve", str(self.pending.id), "--cost", "452000", "--force")
        self.pending.refresh_from_db()
        self.assertEqual(self.pending.status, "APPLIED")

    def test_unpaired_merger_cannot_be_approved(self):
        rows = [("Jindal Stainless (Hisar)", "01-05-2022", "Buy", "4520", "100", "452000"),
                ("Jindal Stainless (Hisar)", "09-03-2023", "*Merged", "4520", "203.12", "918090.51")]
        PendingRestructuring.objects.all().delete()
        self.import_rows(rows)
        pending = PendingRestructuring.objects.get()
        with self.assertRaisesRegex(CommandError, "one \\*Merged and one \\*Merger"):
            self.run_command("--approve", str(pending.id), "--cost", "452000")


class DemergerReviewTests(RestructuringTestBase):
    SPLIT = "Allcargo Terminals=0.10,Transindia Real Estate=0.20"

    def setUp(self):
        super().setUp()
        self.import_rows(DEMERGER_ROWS)
        self.pending = PendingRestructuring.objects.get()

    def approve(self, *extra):
        return self.run_command("--approve", str(self.pending.id), "--cost", "586300", *extra)

    def test_split_is_required_and_nothing_is_guessed(self):
        before = self.transaction_count()
        with self.assertRaisesRegex(CommandError, "--split"):
            self.approve()
        self.assertEqual(self.transaction_count(), before)

    def test_invalid_splits_are_refused(self):
        for bad in ("Allcargo Terminals=0.10", "Allcargo Terminals=0.6,Transindia Real Estate=0.6",
                    "Allcargo Terminals=0,Transindia Real Estate=0.2", "Nobody=0.1,Transindia Real Estate=0.2"):
            with self.assertRaises(CommandError, msg=bad):
                self.approve("--split", bad)
        self.assertEqual(self.transaction_count(), 1)

    def test_approval_moves_cost_to_children_and_keeps_total_and_quantity(self):
        self.approve("--split", self.SPLIT)
        parent = self.position("Allcargo Logistics")
        terminals = self.position("Allcargo Terminals")
        transindia = self.position("Transindia Real Estate")
        self.assertEqual(parent["quantity"], Decimal("5863"))
        self.assertEqual(terminals["quantity"], Decimal("5863"))
        self.assertEqual(terminals["invested_value"], Decimal("58630.0000"))
        self.assertEqual(transindia["invested_value"], Decimal("117260.0000"))
        self.assertEqual(parent["invested_value"], Decimal("410410.0000"))
        total = parent["invested_value"] + terminals["invested_value"] + transindia["invested_value"]
        self.assertEqual(total, Decimal("586300.0000"))   # nothing created or lost
        created = Transaction.objects.filter(source="OTHER", family=self.family)
        signed = sum((t.amount if t.transaction_type == "SELL" else -t.amount) for t in created)
        self.assertEqual(signed, Decimal("0"))             # net cash zero: XIRR unchanged

    def test_the_review_model_rejects_a_blank_identity(self):
        with self.assertRaises(ValidationError):
            PendingRestructuring(family=self.family, kind="MERGER", event_date=date(2023, 1, 1), legs=[]).full_clean()
