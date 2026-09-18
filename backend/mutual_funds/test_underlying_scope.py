from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase

from investments.models import Asset, AssetCategory, Holding
from mutual_funds.models import MutualFundScheme
from mutual_funds.services.official_underlying import OfficialMutualFundUnderlyingService


class MutualFundUnderlyingScopeTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="mf-underlying-scope-test", password="test")

    def create_scheme(self, name, isin, code):
        return MutualFundScheme.objects.create(
            owner=self.user,
            scheme_name=name,
            amc_name="Test AMC",
            scheme_code=code,
            isin_growth=isin,
            is_active=True,
        )

    def create_live_mf_holding(self, name, isin, quantity):
        asset = Asset.objects.create(
            owner=self.user,
            name=name,
            category=AssetCategory.MUTUAL_FUND,
            isin=isin,
            is_active=True,
        )
        return Holding.objects.create(
            owner=self.user,
            asset=asset,
            quantity=quantity,
            average_cost=Decimal("10"),
            invested_value=Decimal("1000"),
            current_price=Decimal("12"),
            current_value=Decimal("1200") if quantity > 0 else Decimal("0"),
        )

    @patch.object(OfficialMutualFundUnderlyingService, "fetch_scheme")
    def test_currently_owned_mf_is_fetched(self, mock_fetch):
        scheme = self.create_scheme("Owned Fund", "INF000000101", "100001")
        self.create_live_mf_holding(scheme.scheme_name, scheme.isin_growth, Decimal("100"))
        result = OfficialMutualFundUnderlyingService.fetch_all_active(owner_ids=[self.user.id])
        self.assertEqual(result["schemes"], 1)
        mock_fetch.assert_called_once_with(scheme)

    @patch.object(OfficialMutualFundUnderlyingService, "fetch_scheme")
    def test_zero_quantity_mf_is_not_fetched(self, mock_fetch):
        scheme = self.create_scheme("Zero Fund", "INF000000102", "100002")
        self.create_live_mf_holding(scheme.scheme_name, scheme.isin_growth, Decimal("0"))
        result = OfficialMutualFundUnderlyingService.fetch_all_active(owner_ids=[self.user.id])
        self.assertEqual(result["schemes"], 0)
        mock_fetch.assert_not_called()

    @patch.object(OfficialMutualFundUnderlyingService, "fetch_scheme")
    def test_multiple_owned_mfs_are_all_detected(self, mock_fetch):
        first = self.create_scheme("First Fund", "INF000000103", "100003")
        second = self.create_scheme("Second Fund", "INF000000104", "100004")
        self.create_live_mf_holding(first.scheme_name, first.isin_growth, Decimal("10"))
        self.create_live_mf_holding(second.scheme_name, second.isin_growth, Decimal("20"))
        self.create_scheme("Database Only Fund", "INF000000105", "100005")
        result = OfficialMutualFundUnderlyingService.fetch_all_active(owner_ids=[self.user.id])
        self.assertEqual(result["schemes"], 2)
        self.assertEqual({c.args[0].id for c in mock_fetch.call_args_list}, {first.id, second.id})

    @patch.object(OfficialMutualFundUnderlyingService, "fetch_scheme")
    def test_newly_owned_mf_becomes_eligible_automatically(self, mock_fetch):
        existing = self.create_scheme("Existing Fund", "INF000000106", "100006")
        new_scheme = self.create_scheme("New Fund", "INF000000107", "100007")
        self.create_live_mf_holding(existing.scheme_name, existing.isin_growth, Decimal("10"))
        first = OfficialMutualFundUnderlyingService.fetch_all_active(owner_ids=[self.user.id])
        self.assertEqual(first["schemes"], 1)
        self.assertEqual(mock_fetch.call_count, 1)
        self.create_live_mf_holding(new_scheme.scheme_name, new_scheme.isin_growth, Decimal("25"))
        second = OfficialMutualFundUnderlyingService.fetch_all_active(owner_ids=[self.user.id])
        self.assertEqual(second["schemes"], 2)
        self.assertEqual(mock_fetch.call_count, 3)
        self.assertEqual(mock_fetch.call_args_list[-1].args[0], new_scheme)

    @patch.object(OfficialMutualFundUnderlyingService, "fetch_scheme")
    def test_sold_mf_becomes_ineligible_automatically(self, mock_fetch):
        scheme = self.create_scheme("Fund To Sell", "INF000000108", "100008")
        holding = self.create_live_mf_holding(scheme.scheme_name, scheme.isin_growth, Decimal("50"))
        first = OfficialMutualFundUnderlyingService.fetch_all_active(owner_ids=[self.user.id])
        self.assertEqual(first["schemes"], 1)
        mock_fetch.assert_called_once_with(scheme)
        holding.quantity = Decimal("0")
        holding.current_value = Decimal("0")
        holding.save(update_fields=["quantity", "current_value"])
        second = OfficialMutualFundUnderlyingService.fetch_all_active(owner_ids=[self.user.id])
        self.assertEqual(second["schemes"], 0)
        self.assertEqual(mock_fetch.call_count, 1)

    @patch.object(OfficialMutualFundUnderlyingService, "fetch_scheme")
    def test_owned_mf_maps_by_name_when_isin_is_missing(self, mock_fetch):
        scheme = self.create_scheme("Fallback Equity Fund", "INF000000109", "100009")
        self.create_live_mf_holding(scheme.scheme_name, None, Decimal("10"))
        result = OfficialMutualFundUnderlyingService.fetch_all_active(owner_ids=[self.user.id])
        self.assertEqual(result["schemes"], 1)
        mock_fetch.assert_called_once_with(scheme)
