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
from .services import quantity_multiplier, resolve_corporate_action_candidates
from .management.commands.monitor_corporate_actions import classify_filing, extract_event_dates
from users.models import FamilyGroup, FamilyMembership, Role
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
