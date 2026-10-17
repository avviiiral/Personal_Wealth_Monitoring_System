from django.test import TestCase

from investments.models import Asset, AssetCategory
from market_data.services.security_resolver import SecurityResolver


class SecurityResolverDatabaseFallbackTests(TestCase):
    def setUp(self):
        self.isin = "INE9TEST00001"

    def test_resolves_symbol_from_another_asset_with_same_isin(self):
        Asset.objects.create(
            name="Known-symbol asset",
            category=AssetCategory.STOCK,
            isin=self.isin,
            symbol="TESTCO.NS",
        )
        Asset.objects.create(
            name="Imported asset without symbol",
            category=AssetCategory.STOCK,
            isin=self.isin,
            symbol=None,
        )

        self.assertEqual(
            SecurityResolver._resolve_from_database_isin(self.isin),
            "TESTCO.NS",
        )

    def test_adds_nse_suffix_to_consistent_unsuffixed_symbol(self):
        Asset.objects.create(
            name="Known-symbol asset",
            category=AssetCategory.STOCK,
            isin=self.isin,
            symbol="TESTCO",
        )

        self.assertEqual(
            SecurityResolver._resolve_from_database_isin(self.isin),
            "TESTCO.NS",
        )

    def test_returns_none_when_database_has_conflicting_symbols(self):
        Asset.objects.create(
            name="NSE candidate",
            category=AssetCategory.STOCK,
            isin=self.isin,
            symbol="TESTCO.NS",
        )
        Asset.objects.create(
            name="BSE candidate",
            category=AssetCategory.STOCK,
            isin=self.isin,
            symbol="OTHERCO.BO",
        )

        self.assertIsNone(
            SecurityResolver._resolve_from_database_isin(self.isin)
        )

    def test_returns_none_when_no_database_symbol_exists(self):
        self.assertIsNone(
            SecurityResolver._resolve_from_database_isin(self.isin)
        )
