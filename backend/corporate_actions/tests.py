from datetime import date, datetime, timezone as dt_timezone
from decimal import Decimal
from types import SimpleNamespace

from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APIClient

from .models import (
    CorporateAction, CorporateActionStatus, CorporateActionType,
    CorporateActionExposure, CorporateActionReconciliation,
    CorporateActionReconciliationStatus, CorporateActionNotification,
)
from .services import quantity_multiplier, resolve_corporate_action_candidates, discover_exposures_and_reconcile, decide_missing_isin_reconciliation, discover_symbol_name_candidates
from .management.commands.monitor_corporate_actions import (
    build_symbol_isin_map, classify_filing, classify_exchange_action, extract_event_dates,
)
from .providers.nse import normalize_nse_action, normalize_nse_symbol
from users.models import FamilyGroup, FamilyMembership, Role
from investments.models import Asset, AssetCategory, Transaction, TransactionType
from .position_adjustment import confirmed_ratio_actions


class CorporateActionServiceTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="corp-action-test", password="test-password")
        self.user.profile.role = Role.ADMIN
        self.user.profile.save(update_fields=["role"])

    def make_action(self, **overrides):
        values = {
            "owner": self.user,
            "action_type": CorporateActionType.STOCK_SPLIT,
            "security_name": "Example Limited",
            "isin": "INE000A01000",
            "symbol": "EXAMPLE",
            "exchange": "NSE",
            "ex_date": date(2025, 6, 15),
            "old_units": Decimal("1"),
            "new_units": Decimal("2"),
            "status": CorporateActionStatus.CONFIRMED,
        }
        values.update(overrides)
        return CorporateAction.objects.create(**values)

    def test_confirmed_split_multiplier_only_applies_after_transaction_date(self):
        self.make_action()
        self.assertEqual(
            quantity_multiplier(owner=self.user, isin="INE000A01000",
                                 transaction_date=date(2025, 1, 1), valuation_date=date(2025, 7, 1)),
            Decimal("2"),
        )
        self.assertEqual(
            quantity_multiplier(owner=self.user, isin="INE000A01000",
                                 transaction_date=date(2025, 7, 1), valuation_date=date(2025, 8, 1)),
            Decimal("1"),
        )

    def test_pending_action_does_not_adjust_quantity(self):
        self.make_action(status=CorporateActionStatus.PENDING)
        self.assertEqual(
            quantity_multiplier(owner=self.user, isin="INE000A01000",
                                 transaction_date=date(2025, 1, 1), valuation_date=date(2025, 7, 1)),
            Decimal("1"),
        )

    def test_ratio_actions_use_asset_family_not_selected_family(self):
        asset_family = FamilyGroup.objects.create(name="Asset family")
        selected_family = FamilyGroup.objects.create(name="Selected family")
        other_member = User.objects.create_user(
            username="corp-action-family-member",
            password="test-password",
        )
        FamilyMembership.objects.create(
            profile=self.user.profile,
            family_group=asset_family,
        )
        FamilyMembership.objects.create(
            profile=self.user.profile,
            family_group=selected_family,
        )
        FamilyMembership.objects.create(
            profile=other_member.profile,
            family_group=asset_family,
        )
        self.user.profile.active_family_group = selected_family
        self.user.profile.save(update_fields=["active_family_group", "updated_at"])

        action = self.make_action(owner=other_member)
        asset = SimpleNamespace(
            family_id=asset_family.id,
            isin="INE000A01000",
            symbol="EXAMPLE",
        )

        self.assertEqual(
            confirmed_ratio_actions(asset=asset, owner=self.user),
            [action],
        )

    def test_exact_isin_match_prevents_name_based_guessing(self):
        self.make_action()
        self.assertEqual(resolve_corporate_action_candidates(
            owner=self.user, isin="INE999Z99999", symbol="EXAMPLE"
        ).count(), 0)

    def test_user_cannot_read_another_users_action(self):
        action = self.make_action()
        other = User.objects.create_user(username="corp-action-other", password="test-password")
        client = APIClient()
        client.force_authenticate(other)
        response = client.get(f"/api/corporate-actions/{action.id}/")
        self.assertEqual(response.status_code, 404)

    def test_reconciliation_has_unique_idempotency_key(self):
        action = self.make_action()
        CorporateActionReconciliation.objects.create(
            action=action, owner=self.user, idempotency_key="a" * 64
        )
        with self.assertRaises(Exception):
            CorporateActionReconciliation.objects.create(
                action=action, owner=self.user, idempotency_key="b" * 64
            )


    def test_missing_isin_name_only_event_is_classified_for_identity_review(self):
        from .audit import build_event_audit
        event = {
            "action_type": "CASH_DIVIDEND", "isin": "", "source_isin": "",
            "source_isin_required": True, "company_name": "Example Limited",
            "ex_date": date(2025, 6, 15), "dividend_per_share": "1.50",
        }
        audit = build_event_audit(event, "NSE_CORPORATE_ACTIONS", "", {})
        self.assertTrue(audit["identity_match_required"])
        self.assertEqual(audit["reconciliation_status"], "IDENTITY_REVIEW_REQUIRED")

    def test_missing_isin_action_requires_user_approval_without_transaction_write(self):
        asset = Asset.objects.create(
            owner=self.user, name="Example Limited", category=AssetCategory.STOCK,
            symbol="EXAMPLE", isin="INE000A01000",
        )
        action = self.make_action(isin="", symbol="EXAMPLE", status=CorporateActionStatus.PENDING)
        stats = discover_exposures_and_reconcile(action)
        reconciliation = CorporateActionReconciliation.objects.get(action=action, owner=self.user)
        self.assertEqual(stats["direct"], 1)
        self.assertEqual(reconciliation.status, CorporateActionReconciliationStatus.PENDING_USER_APPROVAL)
        self.assertEqual(reconciliation.identity_match_method, "SYMBOL")
        self.assertEqual(reconciliation.candidate_asset_ids, [asset.id])
        self.assertFalse(Transaction.objects.filter(asset=asset).exists())

    def test_rejecting_missing_isin_candidate_never_writes_transaction(self):
        asset = Asset.objects.create(
            owner=self.user, name="Example Limited", category=AssetCategory.STOCK,
            symbol="EXAMPLE", isin="INE000A01000",
        )
        action = self.make_action(isin="", symbol="EXAMPLE", status=CorporateActionStatus.PENDING)
        discover_exposures_and_reconcile(action)
        reconciliation = CorporateActionReconciliation.objects.get(action=action, owner=self.user)
        result = decide_missing_isin_reconciliation(
            owner=self.user, reconciliation_id=reconciliation.id, decision="REJECT"
        )
        self.assertEqual(result.status, CorporateActionReconciliationStatus.REJECTED)
        self.assertFalse(Transaction.objects.filter(asset=asset).exists())

    def test_name_fallback_and_ambiguous_candidates_require_selected_asset(self):
        first = Asset.objects.create(
            owner=self.user, name="Example Limited", category=AssetCategory.STOCK,
            symbol="EXAMPLE-A", isin="INE000A01000",
        )
        second = Asset.objects.create(
            owner=self.user, name="Example Limited", category=AssetCategory.STOCK,
            symbol="EXAMPLE-B", isin="INE000A01001",
        )
        action = self.make_action(isin="", symbol="", security_name="Example Limited", status=CorporateActionStatus.PENDING)
        discover_exposures_and_reconcile(action)
        reconciliation = CorporateActionReconciliation.objects.get(action=action, owner=self.user)
        self.assertEqual(reconciliation.identity_match_method, "NAME")
        self.assertCountEqual(reconciliation.candidate_asset_ids, [first.id, second.id])
        with self.assertRaises(Exception):
            decide_missing_isin_reconciliation(
                owner=self.user, reconciliation_id=reconciliation.id, decision="APPROVE"
            )

    def test_legacy_add_transaction_endpoint_is_removed(self):
        action = self.make_action()
        reconciliation = CorporateActionReconciliation.objects.create(
            action=action, owner=self.user, idempotency_key="c" * 64
        )
        CorporateActionExposure.objects.create(
            action=action, owner=self.user, exposure_type="MUTUAL_FUND",
            investment_name="Example Fund", investment_id=1,
        )
        client = APIClient()
        client.force_authenticate(self.user)
        response = client.post(
            f"/api/corporate-actions/reconciliations/{reconciliation.id}/add-transaction/",
            {"quantity": "1", "amount": "0", "confirmed": True},
            format="json",
        )
        self.assertEqual(response.status_code, 404)


    def test_approval_creates_one_idempotent_transaction_for_selected_candidate(self):
        asset = Asset.objects.create(
            owner=self.user, name="Example Limited", category=AssetCategory.STOCK,
            symbol="EXAMPLE", isin="INE000A01000",
        )
        Transaction.objects.create(
            owner=self.user, asset=asset, transaction_type=TransactionType.BUY,
            transaction_date=date(2025, 1, 1), quantity=Decimal("10"),
            price_per_unit=Decimal("100"), amount=Decimal("1000"),
            fees=Decimal("0"), family_name="Member A", portfolio="Direct Equity",
            asset_class="Equity", sub_class="Listed Equity",
            asset_name="Example Limited", underlying="Example Limited",
            advisors="Advisor A",
        )
        action = self.make_action(
            isin="", symbol="EXAMPLE", action_type=CorporateActionType.BONUS,
            old_units=Decimal("2"), new_units=Decimal("1"), status=CorporateActionStatus.PENDING,
        )
        discover_exposures_and_reconcile(action)
        reconciliation = CorporateActionReconciliation.objects.get(action=action, owner=self.user)
        first = decide_missing_isin_reconciliation(
            owner=self.user, reconciliation_id=reconciliation.id, decision="APPROVE", asset_id=asset.id
        )
        second = decide_missing_isin_reconciliation(
            owner=self.user, reconciliation_id=reconciliation.id, decision="APPROVE", asset_id=asset.id
        )
        self.assertEqual(first.status, CorporateActionReconciliationStatus.APPROVED)
        self.assertEqual(second.status, CorporateActionReconciliationStatus.APPROVED)
        self.assertEqual(Transaction.objects.filter(
            asset=asset, transaction_date=action.ex_date, source="OTHER"
        ).count(), 1)
        self.assertEqual(first.matched_transaction_ids, [
            Transaction.objects.get(id=first.matched_transaction_ids[0]).id
        ])
        created_bonus = Transaction.objects.get(id=first.matched_transaction_ids[0])
        self.assertEqual(created_bonus.transaction_type, TransactionType.BONUS)
        self.assertEqual(created_bonus.quantity, Decimal("5"))
        self.assertEqual(created_bonus.family_name, "Member A")
        self.assertEqual(created_bonus.portfolio, "Direct Equity")
        self.assertEqual(created_bonus.asset_class, "Equity")
        self.assertEqual(created_bonus.sub_class, "Listed Equity")
        self.assertEqual(created_bonus.asset_name, "Example Limited")
        self.assertEqual(created_bonus.underlying, "Example Limited")
        self.assertEqual(created_bonus.advisors, "Advisor A")

    def test_bonus_entitlement_is_calculated_separately_for_each_family_member(self):
        asset = Asset.objects.create(
            owner=self.user, name="Trent Limited", category=AssetCategory.STOCK,
            symbol="TRENT", isin="INE849A01020",
        )
        common = {
            "owner": self.user,
            "asset": asset,
            "transaction_type": TransactionType.BUY,
            "transaction_date": date(2025, 1, 1),
            "price_per_unit": Decimal("100"),
            "fees": Decimal("0"),
            "portfolio": "Direct Equity",
            "asset_class": "Equity",
            "sub_class": "Listed Equity",
            "asset_name": "Trent Limited",
            "underlying": "Trent Limited",
        }
        Transaction.objects.create(
            **common, quantity=Decimal("2"), amount=Decimal("200"),
            family_name="Family Member 1",
        )
        Transaction.objects.create(
            **common, quantity=Decimal("4"), amount=Decimal("400"),
            family_name="Family Member 2",
        )
        action = self.make_action(
            isin="", symbol="TRENT", action_type=CorporateActionType.BONUS,
            old_units=Decimal("2"), new_units=Decimal("1"),
            ex_date=date(2025, 6, 15), status=CorporateActionStatus.PENDING,
        )
        discover_exposures_and_reconcile(action)
        reconciliation = CorporateActionReconciliation.objects.get(action=action, owner=self.user)

        approved = decide_missing_isin_reconciliation(
            owner=self.user, reconciliation_id=reconciliation.id,
            decision="APPROVE", asset_id=asset.id,
        )

        bonus_rows = Transaction.objects.filter(
            id__in=approved.matched_transaction_ids,
            transaction_type=TransactionType.BONUS,
            transaction_date=action.ex_date,
        )
        self.assertEqual(bonus_rows.count(), 2)
        quantities_by_member = {
            row.family_name: row.quantity for row in bonus_rows
        }
        self.assertEqual(quantities_by_member, {
            "Family Member 1": Decimal("1"),
            "Family Member 2": Decimal("2"),
        })
        self.assertTrue(all(row.portfolio == "Direct Equity" for row in bonus_rows))
        self.assertTrue(all(row.underlying == "Trent Limited" for row in bonus_rows))

    def test_missing_isin_candidate_discovery_does_not_leak_another_users_asset(self):
        other = User.objects.create_user(username="corp-action-other-owner", password="test-password")
        Asset.objects.create(
            owner=other, name="Example Limited", category=AssetCategory.STOCK,
            symbol="EXAMPLE", isin="INE000A01000",
        )
        action = self.make_action(isin="", symbol="EXAMPLE", status=CorporateActionStatus.PENDING)
        discover_exposures_and_reconcile(action)
        self.assertFalse(CorporateActionReconciliation.objects.filter(action=action, owner=self.user).exists())
        self.assertFalse(CorporateActionNotification.objects.filter(action=action, owner=self.user).exists())

    def test_approved_or_rejected_identity_decision_is_not_reopened_by_rescan(self):
        asset = Asset.objects.create(
            owner=self.user, name="Example Limited", category=AssetCategory.STOCK,
            symbol="EXAMPLE", isin="INE000A01000",
        )
        action = self.make_action(isin="", symbol="EXAMPLE", status=CorporateActionStatus.PENDING)
        discover_exposures_and_reconcile(action)
        reconciliation = CorporateActionReconciliation.objects.get(action=action, owner=self.user)
        decide_missing_isin_reconciliation(
            owner=self.user, reconciliation_id=reconciliation.id, decision="REJECT"
        )
        discover_exposures_and_reconcile(action)
        reconciliation.refresh_from_db()
        self.assertEqual(reconciliation.status, CorporateActionReconciliationStatus.REJECTED)

class CorporateActionAPITests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="corp-action-api", password="test-password")
        self.user.profile.role = Role.ADMIN
        self.user.profile.save(update_fields=["role"])
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def test_create_rejects_ratio_action_without_ratio(self):
        response = self.client.post("/api/corporate-actions/", {
            "action_type": "STOCK_SPLIT",
            "security_name": "Example Limited",
            "isin": "INE000A01000",
            "ex_date": "2025-06-15",
        }, format="json")
        self.assertEqual(response.status_code, 400)

    def test_pending_action_is_not_applied(self):
        response = self.client.post("/api/corporate-actions/", {
            "action_type": "STOCK_SPLIT",
            "security_name": "Example Limited",
            "isin": "INE000A01000",
            "ex_date": "2025-06-15",
            "old_units": "1",
            "new_units": "2",
        }, format="json")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["status"], "PENDING")
        self.assertIsNone(response.data["quantity_factor"])


class FilingClassificationTests(TestCase):
    def test_ignores_rights_issue_monitoring_report(self):
        filing = SimpleNamespace(
            filing_type="",
            subject="Monitoring Agency Report",
            details="Monitoring Agency report for quarter ended September 30, 2026 of Rights issue",
        )
        self.assertIsNone(classify_filing(filing))

    def test_ignores_rights_share_allotment_as_new_action(self):
        filing = SimpleNamespace(
            filing_type="",
            subject="Allotment of Securities",
            details="Allotment of partly paid-up equity shares on Rights Basis",
        )
        self.assertIsNone(classify_filing(filing))

    def test_ignores_buyback_extinguishment_as_new_action(self):
        filing = SimpleNamespace(
            filing_type="",
            subject="General Updates",
            details="Extinguishment of shares pursuant to buyback of equity shares",
        )
        self.assertIsNone(classify_filing(filing))

    def test_ignores_newspaper_copy_even_when_dividend_is_mentioned(self):
        filing = SimpleNamespace(
            filing_type="",
            subject="Copy of Newspaper Publication",
            details="Notice for Record Date for Interim Dividend",
        )
        self.assertIsNone(classify_filing(filing))

    def test_classifies_dividend_declaration_but_not_standalone_record_date_notice(self):
        declaration = SimpleNamespace(
            filing_type="",
            subject="Dividend",
            details="Board of Directors declared Interim Dividend of Rs. 3.05 per equity share.",
        )
        record_notice = SimpleNamespace(
            filing_type="",
            subject="Record Date",
            details="Record date for the purpose of Dividend is 06-Nov-2026.",
        )
        self.assertEqual(classify_filing(declaration), CorporateActionType.CASH_DIVIDEND)
        self.assertIsNone(classify_filing(record_notice))
        self.assertEqual(extract_event_dates(record_notice), {"record_date": date(2026, 11, 6)})

    def test_classifies_bonus_approval_subject_to_shareholder_approval(self):
        filing = SimpleNamespace(
            filing_type="",
            subject="Outcome of Board Meeting",
            details="Approval of the Bonus issue in the ratio 1:1, subject to approval of shareholders.",
        )
        self.assertEqual(classify_filing(filing), CorporateActionType.BONUS)

    def test_announcement_date_is_not_used_as_ex_date(self):
        class FakeFiling:
            filing_type = "Corporate Announcement"
            subject = "Stock split approved"
            details = "The board approved the split on 1 Oct 2026."
        self.assertEqual(classify_filing(FakeFiling()), CorporateActionType.STOCK_SPLIT)
        self.assertEqual(extract_event_dates(FakeFiling()), {})

    def test_extracts_explicit_ex_date_and_other_dates(self):
        class FakeFiling:
            filing_type = "Corporate Announcement"
            subject = "Bonus issue"
            details = "Ex-date: 15/11/2026; Record Date: 16 Nov 2026; Payment date: 30-11-2026"
        self.assertEqual(classify_filing(FakeFiling()), CorporateActionType.BONUS)
        self.assertEqual(extract_event_dates(FakeFiling()), {
            "ex_date": date(2026, 11, 15),
            "record_date": date(2026, 11, 16),
            "payment_date": date(2026, 11, 30),
        })

    def test_classifies_exchange_bonus_wording(self):
        class FakeFiling:
            filing_type = "Corporate Announcement"
            subject = "Issuance of Bonus shares"
            details = "Bonus equity shares in the proportion of 1:2."
        self.assertEqual(classify_filing(FakeFiling()), CorporateActionType.BONUS)

    def test_extracts_ex_bonus_basis_date(self):
        class FakeFiling:
            filing_type = "Corporate Announcement"
            subject = "Issuance of Bonus shares"
            details = "The shares will be traded ex-bonus basis from 04-Jun-2026. Record date: 04-Jun-2026."
        self.assertEqual(extract_event_dates(FakeFiling()), {
            "ex_date": date(2026, 6, 4),
            "record_date": date(2026, 6, 4),
        })

    def test_does_not_treat_announcement_date_as_ex_date_for_bonus_wording(self):
        class FakeFiling:
            filing_type = "Corporate Announcement"
            subject = "Issuance of Bonus shares"
            details = "Board approved a bonus issue on 22-Apr-2026."
        self.assertEqual(extract_event_dates(FakeFiling()), {})

    def test_extracts_month_name_dates_across_exchange_separators(self):
        expected_date = date(2026, 6, 4)
        for raw_date in (
            "04-Jun-2026",
            "04 Jun 2026",
            "04/Jun/2026",
            "04.Jun.2026",
            "2026-06-04",
            "04/06/2026",
        ):
            with self.subTest(raw_date=raw_date):
                filing = SimpleNamespace(
                    subject="Bonus issue",
                    details=f"Ex-date: {raw_date}; Record date: {raw_date}",
                )
                self.assertEqual(
                    extract_event_dates(filing),
                    {"ex_date": expected_date, "record_date": expected_date},
                )



class NseCorporateActionsProviderTests(TestCase):
    def test_symbol_isin_mapping_requires_one_unambiguous_held_asset_isin(self):
        assets = [
            SimpleNamespace(symbol="TRENT.NS", isin="INE849A01020"),
            SimpleNamespace(symbol="trent", isin="INE849A01020"),
            SimpleNamespace(symbol="AMBIGUOUS", isin="INE111A01011"),
            SimpleNamespace(symbol="AMBIGUOUS", isin="INE222A01022"),
            SimpleNamespace(symbol="NOISIN", isin=""),
        ]
        self.assertEqual(
            build_symbol_isin_map(assets),
            {"TRENT": "INE849A01020"},
        )

    def test_normalizes_nse_symbol_suffix(self):
        self.assertEqual(normalize_nse_symbol("AARTIPHARM.NS"), "AARTIPHARM")
        self.assertEqual(normalize_nse_symbol("aartiPharm"), "AARTIPHARM")
        self.assertEqual(normalize_nse_symbol(""), "")

    def test_normalizes_nse_corporate_action_table_fields(self):
        record = normalize_nse_action({
            "symbol": "TRENT",
            "comp": "Trent Limited",
            "series": "EQ",
            "subject": "Dividend - Rs 4 Per Share",
            "faceVal": 1,
            "exDate": "12-Jun-2026",
            "recDate": "12-Jun-2026",
            "bcStartDate": "-",
            "bcEndDate": "-",
        }, "TRENT")
        self.assertEqual(record["symbol"], "TRENT")
        self.assertEqual(record["company_name"], "Trent Limited")
        self.assertEqual(record["purpose"], "Dividend - Rs 4 Per Share")
        self.assertEqual(record["ex_date"], date(2026, 6, 12))
        self.assertEqual(record["record_date"], date(2026, 6, 12))
        self.assertIsNone(record["book_closure_start"])
        self.assertIn("symbol=TRENT", record["source_url"])

    def test_classifies_nse_purpose_wording(self):
        self.assertEqual(
            classify_exchange_action("Dividend - Rs 4 Per Share"),
            CorporateActionType.CASH_DIVIDEND,
        )
        self.assertEqual(
            classify_exchange_action("Bonus 1:2"),
            CorporateActionType.BONUS,
        )
        self.assertEqual(
            classify_exchange_action("Rights 2:25 @ Premium Rs 17/-"),
            CorporateActionType.RIGHTS_ISSUE,
        )
        self.assertEqual(
            classify_exchange_action("Face Value Split (Sub-Division)"),
            CorporateActionType.STOCK_SPLIT,
        )
