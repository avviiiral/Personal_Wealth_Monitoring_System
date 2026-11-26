"""Automatic merger/demerger/buyback terms, shared family terms, and the buyback workflow."""
import hashlib
from datetime import date, datetime, time
from decimal import Decimal
from unittest.mock import patch

from django.test import SimpleTestCase
from django.utils import timezone
from rest_framework.test import APIClient

from filing_intelligence.models import Filing
from investments.models import Holding, SecurityMaster, Transaction
from portfolio.services.holding_engine import HoldingCalculationEngine
from . import reconciliation as ledger
from .announcement_terms import (
    extract_terms, fill_terms_from_announcements, missing_terms, normalise_company, resolve_company,
)
from .entitlements import LegInput, Terms, calculate_entitlement
from .models import CorporateAction, CorporateActionNotification, CorporateActionPosition, CorporateActionType
from .test_ledger_reconciliation import EX_DATE, ISIN, SUCCESSOR_ISIN, LedgerFixture, make_user

D = Decimal

HDFC_SCHEME = (
    "Scheme of Amalgamation: 42 equity shares of face value of Re 1 each of HDFC Bank Limited for every "
    "25 equity shares of face value of Rs 2 each of Housing Development Finance Corporation Limited."
)
RIL_DEMERGER = (
    "Scheme of Arrangement between Reliance Industries Limited and Jio Financial Services Limited "
    "(\"Resulting Company\"). 1 (one) fully paid up equity share of Rs 10 each of the Resulting Company for "
    "every 1 (one) equity share of Rs 10 each of Reliance Industries Limited. The cost of acquisition shall be "
    "apportioned 95.32% to RIL and 4.68% to JFSL."
)
TENDER_BUYBACK = (
    "The Board approved a buyback of 1,02,50,000 equity shares at a price of Rs. 4,150/- per equity share "
    "through the tender offer route on a proportionate basis."
)


class ExtractTermsTests(SimpleTestCase):
    def test_merger_ratio_and_successor_company_are_read_from_the_scheme_text(self):
        found = extract_terms(HDFC_SCHEME, "MERGER", source_name="Housing Development Finance Corporation Limited")
        self.assertEqual((found["old_units"], found["new_units"]), (D("25"), D("42")))
        self.assertEqual(found["resulting_security_name"], "HDFC Bank Limited")
        self.assertNotIn("ambiguous", found)

    def test_demerger_reads_the_defined_resulting_company_and_the_cost_split(self):
        found = extract_terms(RIL_DEMERGER, "DEMERGER", source_name="Reliance Industries Limited", source_symbol="RELIANCE")
        self.assertEqual((found["old_units"], found["new_units"]), (D("1"), D("1")))
        self.assertEqual(found["resulting_security_name"], "Jio Financial Services Limited")
        self.assertEqual(found["cost_allocation_ratio"], D("0.0468"))

    def test_cost_split_that_does_not_add_up_to_100_is_ignored(self):
        text = RIL_DEMERGER.replace("4.68%", "6.68%")
        found = extract_terms(text, "DEMERGER", source_name="Reliance Industries Limited", source_symbol="RELIANCE")
        self.assertNotIn("cost_allocation_ratio", found)

    def test_cost_split_without_both_companies_is_not_guessed(self):
        text = "1 share of Spinco Limited for every 2 shares. 30% of the cost goes to Spinco Limited."
        found = extract_terms(text, "DEMERGER", source_name="Parent Limited")
        self.assertNotIn("cost_allocation_ratio", found)

    def test_buyback_price_and_tender_route(self):
        found = extract_terms(TENDER_BUYBACK, "BUYBACK")
        self.assertEqual(found["cash_per_unit"], D("4150"))
        self.assertEqual(found["buyback_method"], "TENDER")

    def test_open_market_buyback_is_recognised(self):
        found = extract_terms("Buy-back through the open market route at a maximum price of Rs 1,200 per share", "BUYBACK")
        self.assertEqual(found["buyback_method"], "OPEN_MARKET")

    def test_equal_ratios_written_differently_agree(self):
        text = ("1 equity share of Bank of Baroda Limited for every 10 shares; that is 2 shares of Bank of Baroda "
                "Limited for every 20 shares")
        found = extract_terms(text, "MERGER")
        self.assertEqual((found["old_units"], found["new_units"]), (D("10"), D("1")))
        self.assertEqual(found["resulting_security_name"], "Bank of Baroda Limited")

    def test_conflicting_ratios_in_one_text_are_not_used(self):
        text = "1 share of XYZ Limited for every 10 shares; corrigendum: 3 shares of XYZ Limited for every 10 shares"
        found = extract_terms(text, "MERGER")
        self.assertNotIn("old_units", found)
        self.assertEqual(found["ambiguous"]["ratio"], ["10:1", "10:3"])

    def test_text_without_terms_returns_nothing(self):
        self.assertEqual(extract_terms("Amalgamation of ABC Ltd with XYZ Limited", "MERGER"), {})
        self.assertEqual(extract_terms("", "DEMERGER"), {})
        self.assertEqual(extract_terms(None, "BUYBACK"), {})

    def test_company_names_are_compared_without_legal_suffixes(self):
        self.assertEqual(normalise_company("HDFC Bank Ltd."), normalise_company("HDFC BANK LIMITED"))
        self.assertNotEqual(normalise_company("HDFC Bank Limited"), normalise_company("HDFC Limited"))


class BuybackEntitlementTests(SimpleTestCase):
    terms = Terms(action_type="BUYBACK", cash_per_unit=D("1500"))

    def test_without_an_accepted_quantity_nothing_is_planned(self):
        result = calculate_entitlement(self.terms, [LegInput("1", D("100"), D("100000"))])
        self.assertTrue(result.ok)
        self.assertTrue(result.needs_accepted_quantity)
        self.assertEqual(result.entries, [])

    def test_accepted_shares_are_sold_at_the_buyback_price(self):
        result = calculate_entitlement(self.terms, [LegInput("1", D("100"), D("100000"))],
                                       accepted_quantity=D("30"), event_date=date(2025, 1, 10))
        self.assertEqual(len(result.entries), 1)
        entry = result.entries[0]
        self.assertEqual((entry.transaction_type, entry.quantity, entry.amount), ("SELL", D("30"), D("45000.0000")))
        self.assertEqual(result.resulting_quantity, D("70"))
        self.assertIn("deemed dividend", result.explanation)

    def test_tax_note_depends_on_the_buyback_date(self):
        legs = [LegInput("1", D("10"), D("1000"))]
        before = calculate_entitlement(self.terms, legs, accepted_quantity=D("1"), event_date=date(2024, 6, 1))
        after = calculate_entitlement(self.terms, legs, accepted_quantity=D("1"), event_date=date(2026, 6, 1))
        self.assertIn("exempt", before.explanation)
        self.assertIn("capital gain", after.explanation)

    def test_accepted_shares_are_split_across_legs_in_proportion(self):
        legs = [LegInput("1", D("60"), D("600")), LegInput("2", D("40"), D("400"))]
        result = calculate_entitlement(self.terms, legs, accepted_quantity=D("25"))
        self.assertEqual({entry.leg_key: entry.quantity for entry in result.entries}, {"1": D("15"), "2": D("10")})

    def test_invalid_accepted_quantities_need_review(self):
        legs = [LegInput("1", D("10"), D("1000"))]
        for quantity in (D("11"), D("2.5"), D("-1")):
            self.assertEqual(calculate_entitlement(self.terms, legs, accepted_quantity=quantity).status, "MANUAL_REVIEW")

    def test_missing_price_needs_review(self):
        result = calculate_entitlement(Terms(action_type="BUYBACK"), [LegInput("1", D("10"), D("1000"))])
        self.assertEqual(result.status, "MANUAL_REVIEW")
        self.assertIn("price", result.reason)


def make_filing(isin, text, *, published=date(2025, 3, 1), symbol="ABC", subject="Scheme of Arrangement"):
    return Filing.objects.create(
        exchange="NSE", company_name="ABC Ltd", symbol=symbol, isin=isin, subject=subject, details=text,
        filing_url=f"https://example.invalid/{hashlib.sha256(text.encode()).hexdigest()[:12]}",
        published_at=timezone.make_aware(datetime.combine(published, time(10))),
        content_hash=hashlib.sha256((subject + text).encode()).hexdigest(),
    )


class FillTermsTests(LedgerFixture):
    def test_merger_terms_and_successor_isin_are_filled_from_an_announcement(self):
        SecurityMaster.objects.create(family=self.family, isin=SUCCESSOR_ISIN, asset_name="XYZ Industries Ltd")
        make_filing(ISIN, "1 equity share of Rs 10 each of XYZ Industries Limited for every 4 equity shares of ABC Ltd.",
                    subject="Scheme of Amalgamation")
        action = self.make_action(action_type=CorporateActionType.MERGER, old_units=None, new_units=None)
        filled = fill_terms_from_announcements(action)
        action.refresh_from_db()
        self.assertEqual(set(filled), {"old_units", "new_units", "resulting_security_name", "resulting_isin"})
        self.assertEqual((action.old_units, action.new_units), (D("4"), D("1")))
        self.assertEqual(action.resulting_isin, SUCCESSOR_ISIN)
        self.assertIn("announcement #", action.metadata["announcement_terms"]["filled"]["old_units"]["source"])
        self.assertEqual(missing_terms(action), [])

    def test_stored_terms_are_never_overwritten(self):
        make_filing(ISIN, "1 equity share of XYZ Limited for every 4 equity shares.", subject="Amalgamation")
        action = self.make_action(action_type=CorporateActionType.MERGER, old_units=D("1"), new_units=D("3"))
        fill_terms_from_announcements(action)
        action.refresh_from_db()
        self.assertEqual((action.old_units, action.new_units), (D("1"), D("3")))

    def test_announcements_that_disagree_leave_the_ratio_blank(self):
        make_filing(ISIN, "1 equity share of XYZ Limited for every 4 equity shares.", subject="Amalgamation notice")
        make_filing(ISIN, "1 equity share of XYZ Limited for every 5 equity shares.", subject="Amalgamation update",
                    published=date(2025, 4, 1))
        action = self.make_action(action_type=CorporateActionType.MERGER, old_units=None, new_units=None)
        fill_terms_from_announcements(action)
        action.refresh_from_db()
        self.assertIsNone(action.old_units)
        self.assertIn("ratio", action.metadata["announcement_terms"]["ambiguous"])
        self.assertIn("share ratio", missing_terms(action))

    def test_an_unknown_company_name_is_not_resolved_to_an_isin(self):
        self.assertIsNone(resolve_company("Nonexistent Holdings Limited"))
        self.make_asset(name="Twin Co Ltd", isin="INE000TW1011", symbol="TW1")
        self.make_asset(name="Twin Co Limited", isin="INE000TW2022", symbol="TW2")
        self.assertIsNone(resolve_company("Twin Co Limited"))  # two ISINs: ambiguous, not guessed

    def test_a_lookup_failure_leaves_the_event_unchanged_and_reconciliation_continues(self):
        self.tx("BUY", date(2025, 1, 1), 100, 1000)
        make_filing(ISIN, "1 equity share of XYZ Industries Limited for every 4 equity shares.", subject="Amalgamation")
        action = self.make_action(action_type=CorporateActionType.MERGER, old_units=None, new_units=None)
        with patch("corporate_actions.announcement_terms.resolve_company", side_effect=RuntimeError("db down")):
            self.assertEqual(fill_terms_from_announcements(action), [])
            reconciliation = self.reconcile(action)
        action.refresh_from_db()
        self.assertIsNone(action.old_units)
        self.assertEqual(reconciliation.status, "MANUAL_REVIEW")

    def test_a_failed_announcement_query_leaves_the_event_unchanged(self):
        from django.db import DatabaseError

        self.tx("BUY", date(2025, 1, 1), 100, 1000)
        action = self.make_action(action_type=CorporateActionType.DEMERGER, old_units=None, new_units=None)
        with patch("filing_intelligence.models.Filing.objects.filter", side_effect=DatabaseError("connection lost")):
            self.assertEqual(fill_terms_from_announcements(action), [])
            reconciliation = self.reconcile(action)
        action.refresh_from_db()
        self.assertIsNone(action.new_units)
        self.assertEqual(reconciliation.status, "MANUAL_REVIEW")

    def test_each_company_lookup_failing_leaves_the_event_unchanged(self):
        from django.db import DatabaseError

        make_filing(ISIN, "1 equity share of XYZ Industries Limited for every 4 equity shares.", subject="Amalgamation")
        for target in ("investments.models.SecurityMaster.objects.filter", "investments.models.Asset.objects.filter"):
            with self.subTest(failing=target):
                action = self.make_action(action_type=CorporateActionType.MERGER, old_units=None, new_units=None,
                                          ex_date=EX_DATE if "Security" in target else EX_DATE.replace(day=16))
                with patch(target, side_effect=DatabaseError("connection lost")):
                    self.assertEqual(fill_terms_from_announcements(action), [])
                action.refresh_from_db()
                self.assertIsNone(action.old_units)
                self.assertEqual(action.resulting_isin, "")

    def test_booked_events_are_left_alone(self):
        self.tx("BUY", date(2025, 1, 1), 100, 1000)
        action = self.make_action(action_type=CorporateActionType.MERGER, old_units=D("1"), new_units=D("1"),
                                  resulting_isin=SUCCESSOR_ISIN, resulting_security_name="XYZ Ltd")
        self.accept(self.reconcile(action))
        make_filing(ISIN, "Buyback at a price of Rs 100 per equity share", subject="Amalgamation")
        CorporateAction.objects.filter(pk=action.pk).update(resulting_symbol="")
        self.assertEqual(fill_terms_from_announcements(action), [])

    def test_discovery_fills_terms_so_the_merger_proposal_needs_no_typing(self):
        self.tx("BUY", date(2025, 1, 1), 100, 1000)
        SecurityMaster.objects.create(family=self.family, isin=SUCCESSOR_ISIN, asset_name="XYZ Industries Limited")
        make_filing(ISIN, "1 equity share of XYZ Industries Limited for every 4 equity shares of ABC Ltd.",
                    subject="Scheme of Amalgamation")
        action = self.make_action(action_type=CorporateActionType.MERGER, old_units=None, new_units=None)
        reconciliation = self.reconcile(action)
        position = CorporateActionPosition.objects.get(reconciliation=reconciliation)
        self.assertEqual(position.status, "PENDING")
        self.assertEqual(position.new_security_quantity, D("25"))
        self.assertEqual(Transaction.objects.filter(corporate_action_entry__isnull=False).count(), 0)


class SharedFamilyTermsTests(LedgerFixture):
    def setUp(self):
        super().setUp()
        self.member = make_user("ca-family-member")
        self.member.profile.family_groups.add(self.family)
        self.outsider = make_user("ca-outsider")
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def test_terms_entered_once_fill_family_members_copies_and_recalculate(self):
        self.tx("BUY", date(2025, 1, 1), 100, 1000, owner=self.member, family_name="Member B")
        demerger = {"action_type": CorporateActionType.DEMERGER, "old_units": None, "new_units": None}
        mine = self.make_action(**demerger)
        theirs = self.make_action(owner=self.member, **demerger)
        outsider_copy = self.make_action(owner=self.outsider, **demerger)
        their_reconciliation = self.reconcile(theirs)
        self.assertEqual(their_reconciliation.status, "MANUAL_REVIEW")

        response = self.client.patch(f"/api/corporate-actions/{mine.id}/", {
            "old_units": "1", "new_units": "1", "resulting_isin": SUCCESSOR_ISIN,
            "resulting_security_name": "Spinco Ltd", "cost_allocation_ratio": "0.25",
        }, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["shared_with_family_copies"], 1)
        theirs.refresh_from_db()
        outsider_copy.refresh_from_db()
        self.assertEqual((theirs.new_units, theirs.cost_allocation_ratio), (D("1"), D("0.25")))
        self.assertIsNone(outsider_copy.new_units)
        their_reconciliation.refresh_from_db()
        self.assertEqual(their_reconciliation.status, "PENDING")
        self.assertEqual(Transaction.objects.filter(corporate_action_entry__isnull=False).count(), 0)

    def test_a_sharing_failure_never_undoes_the_editors_own_save(self):
        mine = self.make_action(action_type=CorporateActionType.MERGER, old_units=None, new_units=None)
        theirs = self.make_action(owner=self.member, action_type=CorporateActionType.MERGER, old_units=None, new_units=None)
        with patch("corporate_actions.views.share_terms_with_family", side_effect=RuntimeError("boom")):
            response = self.client.patch(f"/api/corporate-actions/{mine.id}/", {"old_units": "1", "new_units": "2"},
                                         format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertIn("could not be copied", response.data["share_warning"])
        mine.refresh_from_db()
        theirs.refresh_from_db()
        self.assertEqual(mine.new_units, D("2"))
        self.assertIsNone(theirs.new_units)

    def test_terms_a_family_member_already_has_are_not_replaced(self):
        mine = self.make_action(action_type=CorporateActionType.MERGER, old_units=None, new_units=None)
        theirs = self.make_action(owner=self.member, action_type=CorporateActionType.MERGER,
                                  old_units=D("1"), new_units=D("3"))
        self.client.patch(f"/api/corporate-actions/{mine.id}/", {"old_units": "1", "new_units": "2"}, format="json")
        theirs.refresh_from_db()
        self.assertEqual(theirs.new_units, D("3"))

    def test_missing_terms_are_reported_to_the_page(self):
        self.tx("BUY", date(2025, 1, 1), 100, 1000)
        action = self.make_action(action_type=CorporateActionType.DEMERGER, old_units=None, new_units=None)
        self.reconcile(action)
        response = self.client.get("/api/corporate-actions/notifications/")
        row = next(item for item in response.data["results"] if item["action"]["id"] == action.id)
        self.assertEqual(row["action"]["missing_terms"], ["share ratio", "new company ISIN", "cost split (%)"])


class BuybackWorkflowTests(LedgerFixture):
    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def buyback(self, **overrides):
        values = {"action_type": CorporateActionType.BUYBACK, "old_units": None, "new_units": None,
                  "cash_per_unit": D("1500"), "record_date": EX_DATE}
        values.update(overrides)
        return self.make_action(**values)

    def accept_with(self, reconciliation, quantities):
        position_ids = {p.family_name: p.id for p in CorporateActionPosition.objects.filter(reconciliation=reconciliation)}
        payload = {str(position_ids[name]): quantity for name, quantity in quantities.items()}
        return self.client.post(f"/api/corporate-actions/reconciliations/{reconciliation.id}/accept/", {
            "confirm": True, "proposal_fingerprint": reconciliation.proposal_fingerprint,
            "accepted_quantities": payload,
        }, format="json")

    def test_buyback_asks_for_the_accepted_quantity_and_books_nothing_on_detection(self):
        self.tx("BUY", date(2025, 1, 1), 100, 100000)
        reconciliation = self.reconcile(self.buyback())
        position = CorporateActionPosition.objects.get(reconciliation=reconciliation)
        self.assertEqual((reconciliation.status, position.status), ("PENDING", "PENDING"))
        self.assertTrue(position.calculation["needs_accepted_quantity"])
        self.assertEqual(position.planned_entries, [])
        self.assertTrue(CorporateActionNotification.objects.filter(reconciliation=reconciliation).exists())

    def test_accepting_with_the_accepted_quantity_books_a_sale_at_the_buyback_price(self):
        self.tx("BUY", date(2025, 1, 1), 100, 100000)
        reconciliation = self.reconcile(self.buyback(payment_date=date(2025, 7, 20)))
        response = self.accept_with(reconciliation, {"Member A": 30})
        self.assertEqual(response.status_code, 200, response.data)
        sale = Transaction.objects.get(corporate_action_entry__isnull=False)
        self.assertEqual((sale.transaction_type, sale.quantity, sale.amount), ("SELL", D("30"), D("45000.0000")))
        self.assertEqual(sale.transaction_date, date(2025, 7, 20))
        self.assertEqual(HoldingCalculationEngine.calculate_position(self.stock)["quantity"], D("70"))
        self.assertEqual(Holding.objects.get(asset=self.stock).quantity, D("70"))
        # Repeating the request books nothing more.
        self.accept_with(reconciliation, {"Member A": 30})
        self.assertEqual(Transaction.objects.filter(corporate_action_entry__isnull=False).count(), 1)

    def test_quantities_are_validated(self):
        self.tx("BUY", date(2025, 1, 1), 100, 100000)
        reconciliation = self.reconcile(self.buyback())
        for bad in (101, 2.5, -1, "x"):
            response = self.accept_with(reconciliation, {"Member A": bad})
            self.assertEqual(response.status_code, 400, (bad, response.data))
        response = self.client.post(f"/api/corporate-actions/reconciliations/{reconciliation.id}/accept/", {
            "confirm": True, "proposal_fingerprint": reconciliation.proposal_fingerprint,
        }, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.accept_with(reconciliation, {"Member A": 0}).status_code, 400)
        self.assertFalse(Transaction.objects.filter(corporate_action_entry__isnull=False).exists())

    def test_one_holding_tendered_and_another_not(self):
        self.tx("BUY", date(2025, 1, 1), 100, 100000)
        self.tx("BUY", date(2025, 1, 1), 50, 50000, family_name="Member B")
        reconciliation = self.reconcile(self.buyback())
        response = self.accept_with(reconciliation, {"Member A": 20, "Member B": 0})
        self.assertEqual(response.status_code, 200, response.data)
        rows = self.positions(reconciliation)
        self.assertEqual(rows[("Member A", "Direct Equity", "Equity", "Listed Equity", "Direct Equity")].status, "APPLIED")
        self.assertEqual(rows[("Member B", "Direct Equity", "Equity", "Listed Equity", "Direct Equity")].status, "REJECTED")
        self.assertEqual(Transaction.objects.filter(corporate_action_entry__isnull=False).count(), 1)

    def test_did_not_tender_is_a_rejection_that_books_nothing(self):
        self.tx("BUY", date(2025, 1, 1), 100, 100000)
        reconciliation = self.reconcile(self.buyback())
        response = self.client.post(f"/api/corporate-actions/reconciliations/{reconciliation.id}/reject/",
                                    {"reason": "Did not tender"}, format="json")
        self.assertEqual(response.status_code, 200)
        reconciliation.refresh_from_db()
        self.assertEqual(reconciliation.status, "REJECTED")
        self.assertFalse(Transaction.objects.filter(corporate_action_entry__isnull=False).exists())

    def test_an_uploaded_sale_at_the_buyback_price_is_already_recorded(self):
        self.tx("BUY", date(2025, 1, 1), 100, 100000)
        self.tx("SELL", date(2025, 7, 10), 30, 45000, price=1500)
        reconciliation = self.reconcile(self.buyback())
        self.assertEqual(reconciliation.status, "ALREADY_RECORDED")

    def test_an_ordinary_market_sale_is_not_mistaken_for_the_buyback(self):
        self.tx("BUY", date(2025, 1, 1), 100, 100000)
        self.tx("SELL", date(2025, 7, 10), 30, 36000, price=1200)
        reconciliation = self.reconcile(self.buyback())
        self.assertEqual(reconciliation.status, "PENDING")

    def test_open_market_buyback_is_informational_only(self):
        self.tx("BUY", date(2025, 1, 1), 100, 100000)
        action = self.buyback(cash_per_unit=None, metadata={"announcement_terms": {"buyback_method": "OPEN_MARKET"}})
        reconciliation = self.reconcile(action)
        self.assertEqual(reconciliation.status, "MANUAL_REVIEW")
        self.assertIn("Open-market buyback", reconciliation.notes)
        self.assertFalse(CorporateActionNotification.objects.filter(reconciliation=reconciliation).exists())
        self.assertEqual(missing_terms(action), [])

    def test_buyback_price_is_read_from_the_announcement(self):
        self.tx("BUY", date(2025, 1, 1), 100, 100000)
        make_filing(ISIN, TENDER_BUYBACK, subject="Buyback of equity shares")
        action = self.buyback(cash_per_unit=None)
        reconciliation = self.reconcile(action)
        action.refresh_from_db()
        self.assertEqual(action.cash_per_unit, D("4150"))
        self.assertEqual(reconciliation.status, "PENDING")

    def test_buyback_without_isin_becomes_a_proposal_after_identity_approval(self):
        self.tx("BUY", date(2025, 1, 1), 100, 100000)
        action = self.buyback(isin="", symbol="ABC")
        from .services import decide_missing_isin_reconciliation, discover_exposures_and_reconcile

        discovered = discover_exposures_and_reconcile(action)
        reconciliation = ledger.CorporateActionReconciliation.objects.get(action=action)
        self.assertEqual(reconciliation.status, "PENDING_USER_APPROVAL", discovered)
        reconciliation = decide_missing_isin_reconciliation(
            owner=self.user, reconciliation_id=reconciliation.id, decision="APPROVE", asset_id=self.stock.id,
        )
        self.assertEqual(reconciliation.status, "PENDING")
        self.assertFalse(Transaction.objects.filter(corporate_action_entry__isnull=False).exists())
        discover_exposures_and_reconcile(action)   # a rescan keeps the approval
        reconciliation.refresh_from_db()
        self.assertEqual(reconciliation.status, "PENDING")
        response = self.accept_with(reconciliation, {"Member A": 10})
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(Transaction.objects.get(corporate_action_entry__isnull=False).quantity, D("10"))
