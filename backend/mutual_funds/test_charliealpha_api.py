from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from mutual_funds.charliealpha_api import (
    charliealpha_category_catalog,
    charliealpha_category_metrics,
    charliealpha_compare,
    charliealpha_fund_detail,
    charliealpha_holdings_periods,
    charliealpha_security_exposure,
)
from mutual_funds.master_models import MFMasterData, MFSourceRun
from mutual_funds.models import AMFIMasterScheme
from mutual_funds.services.charliealpha import SOURCE


class CharlieAlphaReadAPITests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="charliealpha-api-test", password="test-password")
        self.factory = APIRequestFactory()
        self.scheme = AMFIMasterScheme.objects.create(
            scheme_code="990501",
            scheme_name="PWMS CharlieAlpha API Test Fund - Direct Plan - Growth",
            isin_growth="INF990A01501",
            amc_name="PWMS Test AMC",
            plan="Direct Plan",
            option="Growth",
            is_growth_plan=True,
            sebi_universe_active=True,
            level_1_branch="Equity",
            level_2_group="By Market Cap",
            level_3_category="Large Cap",
        )

    def _get(self, path, view, *args, **kwargs):
        request = self.factory.get(path)
        force_authenticate(request, user=self.user)
        return view(request, *args, **kwargs)

    def test_category_catalog_returns_stored_asset_class_groups(self):
        MFSourceRun.objects.create(
            source=SOURCE,
            command="categories",
            status=MFSourceRun.Status.SUCCESS,
            metadata={
                "category_count": 2,
                "asset_classes": ["Equity", "ETF"],
                "category_slugs": ["equity-large-cap", "index-etf"],
                "category_rows": [
                    {"slug": "equity-large-cap", "name": "Large Cap", "asset_class": "Equity"},
                    {"slug": "index-etf", "name": "Index ETF", "asset_class": "ETF"},
                ],
            },
        )

        response = self._get(
            "/api/mutual-fund-analytics/charliealpha/categories/",
            charliealpha_category_catalog,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 2)
        self.assertEqual(response.data["asset_classes"], ["Equity", "ETF"])
        self.assertEqual(response.data["results"][0]["slug"], "equity-large-cap")

    def test_category_metrics_uses_all_horizon_ingestion_as_fallback(self):
        MFSourceRun.objects.create(
            source=SOURCE,
            command="category_metrics",
            status=MFSourceRun.Status.SUCCESS,
            metadata={
                "category_slug": "equity-large-cap",
                "horizon": None,
                "category_medians": {
                    "1y": {"trailing_cagr": {"median": "12.0", "sample_count": 10}},
                    "3y": {"trailing_cagr": {"median": "11.0", "sample_count": 9}},
                },
                "metrics_count": 10,
            },
        )

        request = self.factory.get(
            "/api/mutual-fund-analytics/charliealpha/categories/equity-large-cap/?horizon=3y"
        )
        force_authenticate(request, user=self.user)
        response = charliealpha_category_metrics(request, "equity-large-cap")

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.data["horizon"])
        self.assertEqual(
            response.data["category_medians"]["3y"]["trailing_cagr"]["median"],
            "11.0",
        )

    def test_fund_detail_merges_latest_non_null_values_per_provider_domain(self):
        MFMasterData.objects.create(
            scheme=self.scheme,
            as_of_date=date(2026, 10, 1),
            scheme_code=self.scheme.scheme_code,
            scheme_name=self.scheme.scheme_name,
            nav=Decimal("101.25"),
            nav_as_of=date(2026, 10, 1),
            nav_source=SOURCE,
            field_sources={"nav": {"source": SOURCE, "as_of_date": "2026-10-01"}},
        )
        MFMasterData.objects.create(
            scheme=self.scheme,
            as_of_date=date(2026, 10, 2),
            scheme_code=self.scheme.scheme_code,
            scheme_name=self.scheme.scheme_name,
            return_1y_pct=Decimal("12.5000"),
            field_sources={
                "performance_and_risk": {
                    "source": SOURCE,
                    "as_of_date": "2026-10-02",
                }
            },
        )

        response = self._get(
            f"/api/mutual-fund-analytics/charliealpha/funds/{self.scheme.scheme_code}/",
            charliealpha_fund_detail,
            self.scheme.scheme_code,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["snapshot"]["nav"], Decimal("101.2500"))
        self.assertEqual(response.data["snapshot"]["return_1y_pct"], Decimal("12.5000"))
        self.assertEqual(response.data["snapshot"]["nav_snapshot_as_of"], date(2026, 10, 1))
        self.assertEqual(
            response.data["snapshot"]["performance_and_risk_snapshot_as_of"],
            date(2026, 10, 2),
        )

    def test_fund_detail_accepts_isin_and_rejects_invalid_nav_limit(self):
        request = self.factory.get(
            f"/api/mutual-fund-analytics/charliealpha/funds/{self.scheme.isin_growth}/?nav_limit=bad"
        )
        force_authenticate(request, user=self.user)
        response = charliealpha_fund_detail(request, self.scheme.isin_growth)

        self.assertEqual(response.status_code, 400)
        self.assertIn("nav_limit", response.data["detail"])

    def test_holdings_periods_can_be_read_using_provider_isin(self):
        MFSourceRun.objects.create(
            source=SOURCE,
            command="holdings_periods",
            status=MFSourceRun.Status.SUCCESS,
            metadata={
                "scope": "scheme",
                "schemes": [{
                    "scheme_code": self.scheme.scheme_code,
                    "provider_identifier": self.scheme.isin_growth,
                    "scheme_name": self.scheme.scheme_name,
                    "periods": [{"as_of": "2026-09-30", "holdings_count": 20}],
                }],
            },
        )

        response = self._get(
            f"/api/mutual-fund-analytics/charliealpha/funds/{self.scheme.isin_growth}/holdings-periods/",
            charliealpha_holdings_periods,
            self.scheme.isin_growth,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["scheme_code"], self.scheme.scheme_code)
        self.assertEqual(response.data["periods"][0]["as_of"], "2026-09-30")

    def test_compare_requires_object_payload_and_valid_scheme_identifiers(self):
        request = self.factory.post(
            "/api/mutual-fund-analytics/charliealpha/compare/",
            data=["990501", "990502"],
            format="json",
        )
        force_authenticate(request, user=self.user)
        non_object_response = charliealpha_compare(request)
        self.assertEqual(non_object_response.status_code, 400)

        request = self.factory.post(
            "/api/mutual-fund-analytics/charliealpha/compare/",
            data={"scheme_codes": ["990501", "NOT-AN-ISIN"]},
            format="json",
        )
        force_authenticate(request, user=self.user)
        invalid_code_response = charliealpha_compare(request)
        self.assertEqual(invalid_code_response.status_code, 400)
        self.assertIn("invalid_identifiers", invalid_code_response.data)

    def test_exposure_minimum_percentage_is_compared_numerically(self):
        MFSourceRun.objects.create(
            source=SOURCE,
            command="fund_exposure",
            status=MFSourceRun.Status.SUCCESS,
            metadata={
                "identifier": "HDFCBANK",
                "min_pct": "5",
                "provider_result": {"count": 1, "data": [{"scheme_code": "990501"}]},
            },
        )

        response = self._get(
            "/api/mutual-fund-analytics/charliealpha/exposure/HDFCBANK/?min_pct=5.0",
            charliealpha_security_exposure,
            "HDFCBANK",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["result"]["count"], 1)
