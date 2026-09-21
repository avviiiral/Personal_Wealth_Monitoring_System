from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.db import IntegrityError
from django.test import TestCase

from mutual_funds.models import (
    MutualFundScheme,
    MutualFundTransaction,
    MutualFundTransactionType,
)
from users.models import FamilyGroup

from investments.models import (
    Asset,
    AssetCategory,
    Transaction,
    TransactionSource,
    TransactionType,
)


class FamilyImportIdempotencyTests(TestCase):
    def setUp(self):
        self.family = FamilyGroup.objects.create(name="Import Test Family")
        self.user1 = User.objects.create_user(username="import_user_1")
        self.user2 = User.objects.create_user(username="import_user_2")
        self.user1.profile.family_groups.add(self.family)
        self.user2.profile.family_groups.add(self.family)

    def test_excel_transaction_source_key_is_family_scoped(self):
        asset = Asset.objects.create(
            owner=self.user1,
            family=self.family,
            name="Test Equity",
            category=AssetCategory.STOCK,
            isin="TEST00000001",
        )

        Transaction.objects.create(
            owner=self.user1,
            family=self.family,
            asset=asset,
            transaction_type=TransactionType.BUY,
            transaction_date=date(2026, 9, 1),
            quantity=Decimal("10"),
            price_per_unit=Decimal("100"),
            amount=Decimal("1000"),
            source=TransactionSource.EXCEL,
            source_key="same-row-key",
        )

        with self.assertRaises(IntegrityError):
            Transaction.objects.create(
                owner=self.user2,
                family=self.family,
                asset=asset,
                transaction_type=TransactionType.BUY,
                transaction_date=date(2026, 9, 1),
                quantity=Decimal("10"),
                price_per_unit=Decimal("100"),
                amount=Decimal("1000"),
                source=TransactionSource.EXCEL,
                source_key="same-row-key",
            )

    def test_mutual_fund_transaction_source_key_is_family_scoped(self):
        scheme = MutualFundScheme.objects.create(
            owner=self.user1,
            family=self.family,
            scheme_name="Test Family Fund",
            isin_growth="TESTMF000001",
        )

        MutualFundTransaction.objects.create(
            owner=self.user1,
            family=self.family,
            family_name="Test Family",
            portfolio="Test Portfolio",
            scheme=scheme,
            transaction_type=MutualFundTransactionType.PURCHASE,
            transaction_date=date(2026, 9, 1),
            units=Decimal("10"),
            nav=Decimal("100"),
            amount=Decimal("1000"),
            source_key="same-mf-row-key",
        )

        with self.assertRaises(IntegrityError):
            MutualFundTransaction.objects.create(
                owner=self.user2,
                family=self.family,
                family_name="Test Family",
                portfolio="Test Portfolio",
                scheme=scheme,
                transaction_type=MutualFundTransactionType.PURCHASE,
                transaction_date=date(2026, 9, 1),
                units=Decimal("10"),
                nav=Decimal("100"),
                amount=Decimal("1000"),
                source_key="same-mf-row-key",
            )

    def test_mutual_fund_scheme_is_reused_at_family_scope(self):
        first = MutualFundScheme.objects.create(
            owner=self.user1,
            family=self.family,
            scheme_name="Shared Fund",
            isin_growth="TESTMF000002",
        )

        with self.assertRaises(IntegrityError):
            MutualFundScheme.objects.create(
                owner=self.user2,
                family=self.family,
                scheme_name="Shared Fund",
                isin_growth="TESTMF000003",
            )

        self.assertEqual(
            MutualFundScheme.objects.filter(
                family=self.family,
                scheme_name="Shared Fund",
            ).count(),
            1,
        )
        self.assertEqual(first.owner_id, self.user1.id)
