from django.contrib.auth.models import User
from django.test import TestCase
from unittest.mock import patch

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

    def test_existing_alias_resolves_across_company_name_variants(self):
        alias = SecurityAlias.objects.create(
            family=self.family, asset=self.asset,
            alias="Reliance Industries Ltd.", approved_by=self.user,
        )
        # Simulate a row created before the canonicalization rules were added.
        SecurityAlias.objects.filter(pk=alias.pk).update(
            normalized_alias="RELIANCEINDUSTRIESLTD"
        )
        result = SecurityIdentityResolver.resolve(
            family=self.family, asset_name="Reliance Industry Limited", underlying="",
            isin="", asset_class="Equity", sub_class="Direct Equity",
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

    def test_limited_and_industry_variants_normalize_equally(self):
        variants = [
            "Reliance Industries Limited",
            "Reliance Industries Ltd",
            "Reliance Industries Ltd.",
            "Reliance Ind. Ltd.",
            "Reliance Inds. Ltd.",
        ]
        normalized = {SecurityIdentityResolver.normalize_name(value) for value in variants}
        self.assertEqual(len(normalized), 1)

    def test_name_matching_accepts_industry_and_ltd_variants(self):
        result = SecurityIdentityResolver.resolve(
            family=self.family, asset_name="Reliance Inds. Ltd.", underlying="",
            isin="", asset_class="Equity", sub_class="Direct Equity",
        )
        self.assertEqual(result["status"], "MATCHED")
        self.assertEqual(result["method"], "NORMALIZED_NAME")
        self.assertEqual(result["asset"].id, self.asset.id)

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

    def test_unresolved_explicit_underlying_does_not_fall_back_to_asset_name(self):
        result = SecurityIdentityResolver.resolve(
            family=self.family,
            asset_name="Reliance Industries Limited",
            underlying="Unrelated Strategy Underlying",
            isin="",
            asset_class="Equity",
            sub_class="Direct Equity",
        )
        self.assertEqual(result["status"], "UNRESOLVED")
        self.assertIsNone(result["asset"])

    @patch("investments.services.security_identity.SecurityResolver.resolve_security_from_master")
    def test_global_master_lookup_uses_underlying_only_when_underlying_is_populated(self, mock_master):
        mock_master.return_value = {
            "status": "UNRESOLVED",
            "canonical_name": "",
            "isin": "",
            "yahoo_symbol": "",
        }
        result = SecurityIdentityResolver.resolve(
            family=self.family,
            asset_name="Reliance Industries Limited",
            underlying="Unrelated Strategy Underlying",
            isin="",
            asset_class="Equity",
            sub_class="Direct Equity",
        )
        self.assertEqual(result["status"], "UNRESOLVED")
        mock_master.assert_called_once_with(name="Unrelated Strategy Underlying")

    def test_name_only_match_requires_known_compatible_instrument_type(self):
        result = SecurityIdentityResolver.resolve(
            family=self.family, asset_name="Reliance Industries Limited", underlying="", isin="",
            asset_class="Unknown Asset Class", sub_class="",
        )
        self.assertEqual(result["status"], "UNRESOLVED")
        self.assertIsNone(result["asset"])


    @patch("investments.services.security_identity.SecurityResolver.resolve_security_from_master")
    def test_security_master_name_variant_resolves_to_canonical_identity_without_isin(self, mock_master):
        mock_master.return_value = {
            "status": "MATCHED",
            "canonical_name": "ABB India Limited",
            "isin": "INE117A01022",
            "yahoo_symbol": "ABB.NS",
        }
        result = SecurityIdentityResolver.resolve(
            family=self.family, asset_name="ABB India Ltd.", underlying="",
            isin="", asset_class="Equity", sub_class="Direct Equity",
        )
        self.assertEqual(result["status"], "MATCHED")
        self.assertEqual(result["canonical_name"], "ABB India Limited")
        self.assertEqual(result["isin"], "INE117A01022")
        self.assertIsNone(result["asset"])

    @patch("investments.services.security_identity.SecurityResolver.resolve_security_from_master")
    def test_security_master_isin_resolves_when_uploaded_name_is_blank(self, mock_master):
        mock_master.return_value = {
            "status": "MATCHED",
            "canonical_name": "ABB India Limited",
            "isin": "INE117A01022",
            "yahoo_symbol": "ABB.NS",
        }
        result = SecurityIdentityResolver.resolve(
            family=self.family, asset_name="", underlying="",
            isin="INE117A01022", asset_class="Equity", sub_class="Direct Equity",
        )
        self.assertEqual(result["status"], "MATCHED")
        self.assertEqual(result["canonical_name"], "ABB India Limited")
        self.assertEqual(result["isin"], "INE117A01022")
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
