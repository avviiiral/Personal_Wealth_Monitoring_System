from django.contrib.auth.models import User
from django.test import TestCase

from users.models import FamilyGroup
from investments.models import Asset, AssetCategory, SecurityAlias
from investments.services.security_identity import SecurityIdentityResolver


class SecurityIdentityResolverTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="identity_owner", password="test-password")
        self.family = FamilyGroup.objects.create(name="Identity Family")
        self.other_family = FamilyGroup.objects.create(name="Other Identity Family")
        self.asset = Asset.objects.create(
            owner=self.user, family=self.family, name="Reliance Industries Limited",
            category=AssetCategory.STOCK, isin="INE002A01018",
        )

    def test_exact_isin_resolves_within_family(self):
        result = SecurityIdentityResolver.resolve(
            family=self.family, asset_name="Reliance Industries", underlying="",
            isin="INE002A01018", asset_class="Equity", sub_class="Direct Equity",
        )
        self.assertEqual(result["status"], "MATCHED")
        self.assertEqual(result["method"], "ISIN")
        self.assertEqual(result["asset"].id, self.asset.id)
        self.assertEqual(result["canonical_name"], "Reliance Industries Limited")

    def test_approved_alias_resolves_to_its_family_asset(self):
        SecurityAlias.objects.create(family=self.family, asset=self.asset, alias="RIL", approved_by=self.user)
        result = SecurityIdentityResolver.resolve(
            family=self.family, asset_name="RIL", underlying="RIL", isin="",
            asset_class="Equity", sub_class="Direct Equity",
        )
        self.assertEqual(result["status"], "MATCHED")
        self.assertEqual(result["method"], "APPROVED_ALIAS")
        self.assertEqual(result["asset"].id, self.asset.id)

    def test_matching_never_crosses_family_boundaries(self):
        Asset.objects.create(
            owner=self.user, family=self.other_family, name="Other Family Security",
            category=AssetCategory.STOCK, isin="INE999Z01011",
        )
        result = SecurityIdentityResolver.resolve(
            family=self.family, asset_name="Other Family Security", underlying="",
            isin="", asset_class="Equity", sub_class="Direct Equity",
        )
        self.assertEqual(result["status"], "UNRESOLVED")
        self.assertIsNone(result["asset"])

    def test_ambiguous_normalized_name_is_not_auto_matched(self):
        Asset.objects.create(owner=self.user, family=self.family, name="Duplicate Security", category=AssetCategory.STOCK)
        Asset.objects.create(owner=self.user, family=self.family, name="Duplicate Security", category=AssetCategory.STOCK)
        result = SecurityIdentityResolver.resolve(
            family=self.family, asset_name="Duplicate Security", underlying="", isin="",
            asset_class="Equity", sub_class="Direct Equity",
        )
        self.assertEqual(result["status"], "AMBIGUOUS")
        self.assertIsNone(result["asset"])

    def test_unique_normalized_name_matches_only_compatible_type(self):
        result = SecurityIdentityResolver.resolve(
            family=self.family, asset_name="reliance-industries limited", underlying="",
            isin="", asset_class="Equity", sub_class="Direct Equity",
        )
        self.assertEqual(result["status"], "MATCHED")
        self.assertEqual(result["method"], "NORMALIZED_NAME")
        self.assertEqual(result["asset"].id, self.asset.id)

    def test_explicit_underlying_conflict_is_flagged_without_discarding_isin_match(self):
        result = SecurityIdentityResolver.resolve(
            family=self.family, asset_name="Strategy Portfolio", underlying="Unrelated Security",
            isin="INE002A01018", asset_class="Equity", sub_class="Direct Equity",
        )
        self.assertEqual(result["status"], "CONFLICT")
        self.assertEqual(result["method"], "ISIN")
        self.assertEqual(result["asset"].id, self.asset.id)
        self.assertEqual(result["canonical_name"], "Reliance Industries Limited")

    def test_name_only_match_requires_known_compatible_instrument_type(self):
        result = SecurityIdentityResolver.resolve(
            family=self.family, asset_name="Reliance Industries Limited", underlying="", isin="",
            asset_class="Unknown Asset Class", sub_class="",
        )
        self.assertEqual(result["status"], "UNRESOLVED")
        self.assertIsNone(result["asset"])

    def test_alias_cannot_reference_asset_from_another_family(self):
        other_asset = Asset.objects.create(
            owner=self.user, family=self.other_family, name="Other Security",
            category=AssetCategory.STOCK,
        )
        with self.assertRaises(ValueError):
            SecurityAlias.objects.create(
                family=self.family, asset=other_asset, alias="Other Alias",
                approved_by=self.user,
            )
