from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APIClient

from mutual_funds.models import AMFIMasterScheme
from mutual_funds.master_models import MFMasterData
from watchlist.models import (
    InvestmentProduct,
    MutualFundProduct,
    PerformanceSnapshot,
    ProductType,
)


class MutualFundAnalyticsApiTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="mf-analytics-user", password="test")
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def _create_fund(self, name, identity, category, return_1y, aum="100"):
        product = InvestmentProduct.objects.create(
            product_type=ProductType.MUTUAL_FUND,
            name=name,
            provider="Example AMC",
            category=category,
            isin=f"ISIN-{identity}",
            identity_key=f"MUTUAL_FUND:TEST:{identity}",
            source="TEST",
        )
        MutualFundProduct.objects.create(
            product=product,
            scheme_code=identity,
            plan="Direct",
            option="Growth",
            aum=Decimal(aum),
        )
        PerformanceSnapshot.objects.create(
            product=product,
            date=date(2026, 10, 28),
            return_1y=Decimal(return_1y) if return_1y is not None else None,
            source="TEST",
        )
        return product

    def test_overview_ranks_funds_and_excludes_missing_returns_from_average(self):
        leader = self._create_fund("Alpha Multi Cap", "ALPHA", "Multi Cap", "12.50", "250")
        self._create_fund("Beta Multi Cap", "BETA", "Multi Cap", "7.50", "150")
        self._create_fund("Gamma Multi Cap", "GAMMA", "Multi Cap", None, "100")
        self._create_fund("Other Category Fund", "OTHER", "Large Cap", "99.00", "500")

        response = self.client.get(
            "/api/mutual-fund-analytics/overview/?category=Multi%20Cap&ranking_period=1Y"
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["summary"]["fund_count"], 3)
        self.assertEqual(response.data["summary"]["funds_with_metrics"], 2)
        self.assertEqual(
            Decimal(str(response.data["summary"]["average_returns"]["1Y"]["average_return"])),
            Decimal("10.00"),
        )
        self.assertEqual(response.data["summary"]["average_returns"]["1Y"]["covered_funds"], 2)
        self.assertEqual(response.data["rankings"][0]["id"], leader.id)
        self.assertEqual(
            [item["name"] for item in response.data["rankings"]],
            ["Alpha Multi Cap", "Beta Multi Cap"],
        )
        self.assertEqual(
            Decimal(str(response.data["summary"]["aum"])),
            Decimal("500"),
        )

    def test_watchlist_exposes_latest_global_research_metrics(self):
        product = self._create_fund("Alpha Multi Cap", "ALPHA", "Multi Cap", "12.50")
        scheme = AMFIMasterScheme.objects.create(
            scheme_code="ALPHA",
            scheme_name="Alpha Multi Cap",
            isin_growth=product.isin,
            amc_name="Example AMC",
            plan="Direct Plan",
            option="Growth",
            is_active=True,
        )
        MFMasterData.objects.create(
            scheme=scheme,
            as_of_date=date(2026, 10, 31),
            scheme_code="ALPHA",
            scheme_name="Alpha Multi Cap",
            amc="Example AMC",
            plan="Direct Plan",
            option="Growth",
            aum_cr=Decimal("1250.5000"),
            aum_type="MONTH_END",
            aum_as_of=date(2026, 10, 31),
            aum_source="AMC_MONTH_END_AUM",
            ter_direct_pct=Decimal("0.5500"),
            std_dev_pct=Decimal("17.2500"),
            sharpe=Decimal("1.2300"),
            risk_free_rate_used_pct=Decimal("6.5000"),
            field_sources={
                "aum_cr": {"source": "AMC_MONTH_END_AUM", "as_of_date": "2026-10-31"},
                "std_dev_pct": {"source": "PWMS_CALCULATED_NAV", "as_of_date": "2026-10-31"},
                "sharpe": {"source": "PWMS_CALCULATED_NAV", "as_of_date": "2026-10-31"},
            },
        )

        response = self.client.get("/api/watch-list/products/?product_type=MUTUAL_FUND")

        self.assertEqual(response.status_code, 200)
        row = next(item for item in response.data["results"] if item["id"] == product.id)
        metrics = row["mutual_fund"]["research_metrics"]
        self.assertEqual(Decimal(str(metrics["aum_cr"])), Decimal("1250.5000"))
        self.assertEqual(metrics["aum_type"], "MONTH_END")
        self.assertEqual(Decimal(str(metrics["ter_direct_pct"])), Decimal("0.5500"))
        self.assertEqual(Decimal(str(metrics["std_dev_pct"])), Decimal("17.2500"))
        self.assertEqual(Decimal(str(metrics["sharpe"])), Decimal("1.2300"))
        self.assertEqual(metrics["field_sources"]["sharpe"]["source"], "PWMS_CALCULATED_NAV")

    def test_overview_requires_authentication(self):
        self.client.force_authenticate(user=None)
        response = self.client.get("/api/mutual-fund-analytics/overview/")
        self.assertEqual(response.status_code, 403)
