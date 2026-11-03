from datetime import date
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase

from investments.models import Asset, AssetCategory, SecurityIdentifierMapping, SecurityIdentityDiscoveryCheckpoint
from investments.services.historical_identity_discovery import (
    HistoricalIdentityDiscoveryService, IdentifierEvidence
)
from users.models import FamilyGroup


class HistoricalIdentityDiscoveryTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="identity_history", password="pw")
        self.family = FamilyGroup.objects.create(name="Identity History Family")
        self.asset = Asset.objects.create(
            owner=self.user, family=self.family, name="Example Limited",
            category=AssetCategory.STOCK, isin="INE000A01011",
        )

    def evidence(self, **overrides):
        values = dict(
            asset_id=self.asset.pk, identifier_type="ISIN", identifier_value="INE000A01011",
            exchange="NSE", valid_from=date(2010, 1, 1), valid_to=date(2016, 9, 11),
            source="OFFICIAL_ANNOUNCEMENT",
            source_reference="https://www.nseindia.com/example.pdf",
            verification_status="VERIFIED", confidence="0.9900",
            evidence={"document": "official"},
        )
        values.update(overrides)
        return IdentifierEvidence(**values)

    def test_verified_historical_isin_resolves_only_inside_interval(self):
        HistoricalIdentityDiscoveryService.persist_evidence(self.evidence())
        result = HistoricalIdentityDiscoveryService.resolve_identifier(
            identifier_type="ISIN", value="INE000A01011", exchange="NSE",
            event_date=date(2016, 9, 10))
        self.assertEqual(result["status"], "MATCHED")
        outside = HistoricalIdentityDiscoveryService.resolve_identifier(
            identifier_type="ISIN", value="INE000A01011", exchange="NSE",
            event_date=date(2016, 9, 12))
        self.assertEqual(outside["status"], "UNRESOLVED")

    def test_pending_evidence_never_resolves(self):
        HistoricalIdentityDiscoveryService.persist_evidence(
            self.evidence(verification_status="PENDING"))
        result = HistoricalIdentityDiscoveryService.resolve_identifier(
            identifier_type="ISIN", value="INE000A01011", exchange="NSE",
            event_date=date(2015, 1, 1))
        self.assertEqual(result["status"], "UNRESOLVED")

    def test_dry_run_does_not_persist(self):
        result = HistoricalIdentityDiscoveryService.persist_evidence(
            self.evidence(), dry_run=True)
        self.assertEqual(result, "would_create")
        self.assertEqual(SecurityIdentifierMapping.objects.count(), 0)

    def test_duplicate_discovery_is_idempotent(self):
        item = self.evidence()
        HistoricalIdentityDiscoveryService.persist_evidence(item)
        HistoricalIdentityDiscoveryService.persist_evidence(item)
        self.assertEqual(SecurityIdentifierMapping.objects.count(), 1)

    def test_identifier_conflict_is_quarantined(self):
        other = Asset.objects.create(
            owner=self.user, family=self.family, name="Other Limited",
            category=AssetCategory.STOCK, isin="INE999A01011")
        HistoricalIdentityDiscoveryService.persist_evidence(self.evidence())
        result = HistoricalIdentityDiscoveryService.persist_evidence(
            self.evidence(asset_id=other.pk))
        self.assertEqual(result, "conflict")
        self.assertFalse(SecurityIdentifierMapping.objects.filter(
            verification_status="VERIFIED").exists())

    def test_transaction_ledger_is_not_modified_by_discovery(self):
        from investments.models import Transaction, TransactionType
        tx = Transaction.objects.create(
            owner=self.user, family=self.family, asset=self.asset,
            transaction_type=TransactionType.BUY, transaction_date=date(2015, 1, 1),
            quantity="10", price_per_unit="100", amount="1000")
        before = (tx.transaction_date, tx.quantity, tx.amount)
        HistoricalIdentityDiscoveryService.persist_evidence(self.evidence())
        tx.refresh_from_db()
        self.assertEqual((tx.transaction_date, tx.quantity, tx.amount), before)

    def test_checkpoint_model_is_available_for_resume(self):
        row = SecurityIdentityDiscoveryCheckpoint.objects.create(
            provider="TEST", scope=f"asset:{self.asset.pk}")
        self.assertEqual(row.provider, "TEST")
