from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from mutual_funds.models import AMFIMasterScheme
from mutual_funds.master_models import MFMasterData


class MutualFundMasterDataAPITests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="mf-masterdata-api",
            password="test-password",
        )
        self.client = APIClient()
        self.scheme = AMFIMasterScheme.objects.create(
            scheme_code="990101",
            scheme_name="PWMS Large Cap Fund",
            isin_growth="INF990A01011",
            amc_name="PWMS Mutual Fund",
            plan="Direct Plan",
            option="Growth",
            is_growth_plan=True,
            sebi_universe_active=True,
            level_3_category="Large Cap",
        )
        MFMasterData.objects.create(
            scheme=self.scheme,
            as_of_date=date(2026, 9, 30),
            scheme_code=self.scheme.scheme_code,
            scheme_name=self.scheme.scheme_name,
            amc=self.scheme.amc_name,
            plan=self.scheme.plan,
            sebi_category_level_3="Large Cap",
            aum_cr=Decimal("100.0000"),
            aum_type="QUARTERLY_AVG",
        )
        MFMasterData.objects.create(
            scheme=self.scheme,
            as_of_date=date(2026, 10, 31),
            scheme_code=self.scheme.scheme_code,
            scheme_name=self.scheme.scheme_name,
            amc=self.scheme.amc_name,
            plan=self.scheme.plan,
            sebi_category_level_3="Large Cap",
            aum_cr=Decimal("110.0000"),
            aum_type="MONTH_END",
        )

    def test_endpoint_requires_authentication(self):
        response = self.client.get("/api/mutual-fund-analytics/masterdata/")
        self.assertIn(response.status_code, (401, 403))

    def test_returns_latest_snapshot_per_scheme(self):
        self.client.force_authenticate(self.user)
        response = self.client.get("/api/mutual-fund-analytics/masterdata/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["as_of_date"], "2026-10-31")
        self.assertEqual(response.data["results"][0]["aum_type"], "MONTH_END")

    def test_category_amc_plan_and_scheme_code_filters(self):
        self.client.force_authenticate(self.user)
        url = (
            "/api/mutual-fund-analytics/masterdata/"
            "?category=Large%20Cap&amc=PWMS&plan=Direct%20Plan&scheme_code=990101"
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["scheme_code"], "990101")

    def test_search_filter_matches_scheme_name_or_code(self):
        self.client.force_authenticate(self.user)
        response = self.client.get("/api/mutual-fund-analytics/masterdata/?q=990101")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 1)

    def test_write_methods_are_not_exposed(self):
        self.client.force_authenticate(self.user)
        response = self.client.post(
            "/api/mutual-fund-analytics/masterdata/",
            {},
            format="json",
        )
        self.assertEqual(response.status_code, 405)
