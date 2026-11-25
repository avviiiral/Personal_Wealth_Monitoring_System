"""Watch List: AUM sort uses the displayed value; MF Intelligence fills gaps only."""
from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APIClient

from mf_intelligence.models import MFCategory, MFScheme
from users.models import FamilyGroup
from watchlist.models import InvestmentProduct, MutualFundProduct, PMSProduct, PerformanceSnapshot, ProductType

INTELLIGENCE_AUM = Decimal("500.00")
PMS_AUM = Decimal("300.00")
WATCHLIST_AUM = Decimal("100.00")
INTELLIGENCE_RETURN_1Y = Decimal("12.5000")
WATCHLIST_RETURN_1Y = Decimal("8.0000")


class WatchListAumAndGapFillTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="aum-user", password="pw")
        family = FamilyGroup.objects.create(name="AUM Family")
        self.user.profile.family_groups.add(family)
        self.client = APIClient()
        self.client.force_authenticate(self.user)

        self.intelligence_only = self._mf("Fund With Intelligence AUM", scheme_code="100001", aum=None)
        MFScheme.objects.create(amfi_code="100001", name="Fund With Intelligence AUM", avg_aum_crore=INTELLIGENCE_AUM,
                                return_1y=INTELLIGENCE_RETURN_1Y, amc="Example AMC")
        self.own_aum = self._mf("Fund With Own AUM", scheme_code="100002", aum=WATCHLIST_AUM)
        MFScheme.objects.create(amfi_code="100002", name="Fund With Own AUM", avg_aum_crore=Decimal("999.00"))
        self.no_aum = self._mf("Fund Without AUM", scheme_code="100003", aum=None)
        PerformanceSnapshot.objects.create(product=self.no_aum, date=date(2026, 11, 20), return_1m=Decimal("1.0"))
        self.pms = InvestmentProduct.objects.create(product_type=ProductType.PMS, name="Some PMS", identity_key="PMS:X")
        PMSProduct.objects.create(product=self.pms, aum=PMS_AUM)
        PerformanceSnapshot.objects.create(product=self.pms, date=date(2026, 11, 20), return_1m=Decimal("1.0"))

    def _mf(self, name, *, scheme_code, aum):
        product = InvestmentProduct.objects.create(
            product_type=ProductType.MUTUAL_FUND, name=name, identity_key=f"AMFI:{scheme_code}",
            external_identifier=scheme_code,
        )
        MutualFundProduct.objects.create(product=product, scheme_code=scheme_code, aum=aum)
        return product

    def _names(self, ordering):
        response = self.client.get("/api/watch-list/products/", {"ordering": ordering, "page_size": 50})
        self.assertEqual(response.status_code, 200)
        return [row["name"] for row in response.json()["results"]]

    def _displayed_aum(self, row):
        return (row.get("mutual_fund") or {}).get("aum") or (row.get("pms") or {}).get("aum")

    def test_aum_sort_descending_matches_the_displayed_value(self):
        names = self._names("-aum")
        self.assertEqual(names, ["Fund With Intelligence AUM", "Some PMS", "Fund With Own AUM", "Fund Without AUM"])

    def test_aum_sort_ascending_keeps_missing_values_last(self):
        names = self._names("aum")
        self.assertEqual(names, ["Fund With Own AUM", "Some PMS", "Fund With Intelligence AUM", "Fund Without AUM"])

    def test_sort_order_and_displayed_aum_agree(self):
        response = self.client.get("/api/watch-list/products/", {"ordering": "-aum", "page_size": 50})
        values = [self._displayed_aum(row) for row in response.json()["results"]]
        numbers = [Decimal(str(value)) for value in values if value is not None]
        self.assertEqual(numbers, sorted(numbers, reverse=True))
        self.assertIsNone(values[-1])

    def test_watch_list_value_is_never_overridden(self):
        response = self.client.get("/api/watch-list/products/", {"ordering": "name", "page_size": 50})
        rows = {row["name"]: row for row in response.json()["results"]}
        self.assertEqual(Decimal(str(rows["Fund With Own AUM"]["mutual_fund"]["aum"])), WATCHLIST_AUM)

    def test_returns_are_filled_from_mf_intelligence_without_a_snapshot(self):
        response = self.client.get(f"/api/watch-list/products/{self.intelligence_only.id}/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Decimal(str(response.json()["metrics"]["1Y"])), INTELLIGENCE_RETURN_1Y)

    def test_blank_provider_and_category_are_filled_from_mf_intelligence(self):
        category = MFCategory.objects.create(slug="flexi-cap", name="Flexi Cap")
        MFScheme.objects.filter(amfi_code="100001").update(category=category)
        payload = self.client.get(f"/api/watch-list/products/{self.intelligence_only.id}/").json()
        self.assertEqual(payload["provider"], "Example AMC")
        self.assertEqual(payload["category"], "Flexi Cap")

    def test_existing_provider_and_category_are_kept(self):
        InvestmentProduct.objects.filter(pk=self.intelligence_only.pk).update(provider="Own Provider", category="Own Category")
        payload = self.client.get(f"/api/watch-list/products/{self.intelligence_only.id}/").json()
        self.assertEqual(payload["provider"], "Own Provider")
        self.assertEqual(payload["category"], "Own Category")
