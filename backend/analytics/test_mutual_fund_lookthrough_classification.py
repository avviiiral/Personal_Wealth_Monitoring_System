from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase

from investments.models import Asset, AssetCategory, SecurityMaster
from mutual_funds.models import MutualFundScheme, MutualFundUnderlying
from analytics.services.mutual_fund_lookthrough import MutualFundLookThroughService


class MutualFundLookThroughClassificationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="mf-classification-user", password="pw")
        self.scheme = MutualFundScheme.objects.create(
            owner=self.user,
            scheme_name="Test Fund",
            scheme_code="MF-CLASS-1",
            is_active=True,
        )

    def _underlying(self, *, isin=None, name="Underlying Security", sector=None):
        return MutualFundUnderlying(
            scheme=self.scheme,
            security_name=name,
            isin=isin,
            security_key=isin or name.upper(),
            percentage_of_nav=Decimal("10"),
            sector=sector,
            portfolio_date="2026-09-15",
            source="AMFI",
        )

    def test_classification_precedence_is_isin_asset_then_name_asset_then_security_master(self):
        asset = Asset.objects.create(
            owner=self.user,
            name="Canonical Asset Name",
            isin="INF123",
            category=AssetCategory.STOCK,
            currency="INR",
            is_active=True,
        )
        master = SecurityMaster.objects.create(
            owner=self.user,
            asset_name="Canonical Asset Name",
            isin="INF123",
            sector="Security Master Sector",
        )
        asset.security_master = master
        asset.save(update_fields=["security_master"])

        by_isin, by_name, security_by_isin, security_by_name = MutualFundLookThroughService._classification_maps(self.user)
        asset_class, sector = MutualFundLookThroughService.classify(
            self._underlying(isin="INF123", name="Different Name", sector="Disclosed Sector"),
            by_isin,
            by_name,
            security_by_isin,
            security_by_name,
        )
        self.assertEqual(asset_class, AssetCategory.STOCK)
        self.assertEqual(sector, "Disclosed Sector")

        asset.isin = None
        asset.save(update_fields=["isin"])
        by_isin, by_name, security_by_isin, security_by_name = MutualFundLookThroughService._classification_maps(self.user)
        asset_class, sector = MutualFundLookThroughService.classify(
            self._underlying(isin=None, name="Canonical Asset Name", sector=None),
            by_isin,
            by_name,
            security_by_isin,
            security_by_name,
        )
        self.assertEqual(asset_class, AssetCategory.STOCK)
        self.assertEqual(sector, "Security Master Sector")

        asset.name = "Another Asset Name"
        asset.save(update_fields=["name"])
        by_isin, by_name, security_by_isin, security_by_name = MutualFundLookThroughService._classification_maps(self.user)
        asset_class, sector = MutualFundLookThroughService.classify(
            self._underlying(isin="INF123", name="Canonical Asset Name", sector=None),
            by_isin,
            by_name,
            security_by_isin,
            security_by_name,
        )
        self.assertIsNone(asset_class)
        self.assertEqual(sector, "Security Master Sector")

    def test_unresolved_security_without_disclosed_or_master_sector_stays_unclassified(self):
        by_isin, by_name, security_by_isin, security_by_name = MutualFundLookThroughService._classification_maps(self.user)
        asset_class, sector = MutualFundLookThroughService.classify(
            self._underlying(isin="UNKNOWN", name="Unknown Security", sector=None),
            by_isin,
            by_name,
            security_by_isin,
            security_by_name,
        )
        self.assertIsNone(asset_class)
        self.assertIsNone(sector)
