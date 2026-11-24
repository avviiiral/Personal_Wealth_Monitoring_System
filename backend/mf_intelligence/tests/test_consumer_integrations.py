from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from mf_intelligence.models import MFCategory, MFHolding, MFHoldingSnapshot, MFScheme
from mutual_funds.models import MutualFundNAV, MutualFundScheme
from mutual_funds.services.holding_engine import MutualFundHoldingEngine
from mutual_funds.services.nav_service import MutualFundNAVService
from users.models import FamilyGroup
from watchlist.models import InvestmentProduct, MutualFundProduct, ProductType


class MFIntelligenceConsumerIntegrationTests(TestCase):
    def setUp(self):
        self.today = timezone.localdate()

    def test_sip_nav_service_uses_recent_synced_nav_before_network_backfill(self):
        scheme = MutualFundScheme.objects.create(
            scheme_name="Synced Equity Fund",
            scheme_code="123456",
        )
        nav_date = self.today - timedelta(days=1)
        MFScheme.objects.create(
            amfi_code="123456",
            name="Synced Equity Fund - Direct Plan - Growth",
            latest_nav=Decimal("42.125000"),
            latest_nav_date=nav_date,
        )

        with patch(
            "mutual_funds.services.nav_service.AMFIService.import_historical_master_navs"
        ) as import_history:
            nav = MutualFundNAVService.get_nav_for_date(scheme, nav_date)

        self.assertEqual(nav.nav, Decimal("42.125000"))
        self.assertEqual(nav.date, nav_date)
        self.assertEqual(nav.source, "MF_INTELLIGENCE")
        import_history.assert_not_called()

    def test_sip_nav_service_does_not_keep_an_older_family_copy_over_newer_synced_nav(self):
        scheme = MutualFundScheme.objects.create(
            scheme_name="Updated Synced Equity Fund",
            scheme_code="123460",
        )
        older_date = self.today - timedelta(days=3)
        newer_date = self.today - timedelta(days=1)
        MutualFundNAV.objects.create(
            scheme=scheme,
            date=older_date,
            nav=Decimal("40.000000"),
            source="AMFI",
        )
        MFScheme.objects.create(
            amfi_code="123460",
            name="Updated Synced Equity Fund - Direct Plan - Growth",
            latest_nav=Decimal("43.500000"),
            latest_nav_date=newer_date,
        )

        with patch(
            "mutual_funds.services.nav_service.AMFIService.import_historical_master_navs"
        ) as import_history:
            nav = MutualFundNAVService.get_nav_for_date(scheme, self.today)

        self.assertEqual(nav.nav, Decimal("43.500000"))
        self.assertEqual(nav.date, newer_date)
        self.assertEqual(nav.source, "MF_INTELLIGENCE")
        import_history.assert_not_called()

    def test_holding_valuation_prefers_newer_recent_synced_nav(self):
        scheme = MutualFundScheme.objects.create(
            scheme_name="Valuation Equity Fund",
            scheme_code="123457",
        )
        older_date = self.today - timedelta(days=2)
        newer_date = self.today - timedelta(days=1)
        MutualFundNAV.objects.create(
            scheme=scheme,
            date=older_date,
            nav=Decimal("30.000000"),
            source="AMFI",
        )
        MFScheme.objects.create(
            amfi_code="123457",
            name="Valuation Equity Fund - Regular Plan - Growth",
            latest_nav=Decimal("31.500000"),
            latest_nav_date=newer_date,
        )

        nav = MutualFundHoldingEngine.get_latest_nav(scheme)

        self.assertEqual(nav.nav, Decimal("31.500000"))
        self.assertEqual(nav.date, newer_date)
        self.assertEqual(nav.source, "MF_INTELLIGENCE")

    def test_watch_list_uses_synced_scheme_metrics_and_risk_data(self):
        user = User.objects.create_user(username="mfi-watchlist-user", password="pw")
        family = FamilyGroup.objects.create(name="MFI Watch List Family")
        user.profile.family_groups.add(family)
        category = MFCategory.objects.create(
            slug="integration-equity",
            name="Integration Equity",
            asset_class="Equity",
        )
        scheme = MFScheme.objects.create(
            amfi_code="123458",
            name="Integration Equity Fund - Direct Plan - Growth",
            category=category,
            plan="Direct",
            option="Growth",
            data_as_of=self.today,
            avg_aum_crore=Decimal("1250.0000"),
            ter=Decimal("0.4500"),
            latest_nav=Decimal("55.250000"),
            latest_nav_date=self.today - timedelta(days=1),
            return_1w=Decimal("1.2500"),
            return_1m=Decimal("2.5000"),
            return_3m=Decimal("5.0000"),
            return_6m=Decimal("8.0000"),
            return_1y=Decimal("12.0000"),
            return_3y=Decimal("14.0000"),
            return_5y=Decimal("13.0000"),
            return_since_inception=Decimal("11.0000"),
            calendar_returns={"2025": "10.2500"},
        )
        product = InvestmentProduct.objects.create(
            product_type=ProductType.MUTUAL_FUND,
            name=scheme.name,
            external_identifier="123458",
            source="AMFI",
            identity_key="MUTUAL_FUND:SCHEME:123458",
        )
        MutualFundProduct.objects.create(
            product=product,
            scheme_code="123458",
            plan="Direct",
            option="Growth",
        )

        client = APIClient()
        client.force_authenticate(user)
        response = client.get("/api/watch-list/products/?product_type=MUTUAL_FUND")

        self.assertEqual(response.status_code, 200)
        rows = response.data["results"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["metrics"]["1Y"], Decimal("12.0000"))
        self.assertEqual(rows[0]["metrics"]["3Y"], Decimal("14.0000"))
        self.assertEqual(rows[0]["metrics"]["CAGR"], None)
        self.assertEqual(rows[0]["mutual_fund"]["latest_nav"], Decimal("55.250000"))
        self.assertEqual(rows[0]["mutual_fund"]["aum"], Decimal("1250.0000"))
        self.assertEqual(rows[0]["mutual_fund"]["expense_ratio"], Decimal("0.4500"))
        self.assertEqual(rows[0]["mutual_fund"]["intelligence"]["category"], "Integration Equity")

    def test_underlying_api_uses_newer_synced_disclosure(self):
        user = User.objects.create_user(username="mfi-underlying-user", password="pw")
        family = FamilyGroup.objects.create(name="MFI Underlying Family")
        user.profile.family_groups.add(family)
        scheme = MutualFundScheme.objects.create(
            owner=user,
            family=family,
            scheme_name="Underlying Equity Fund",
            scheme_code="123459",
        )
        category = MFCategory.objects.create(
            slug="integration-underlying",
            name="Integration Underlying",
            asset_class="Equity",
        )
        intelligence_scheme = MFScheme.objects.create(
            amfi_code="123459",
            name="Underlying Equity Fund - Regular Plan - Growth",
            category=category,
        )
        snapshot = MFHoldingSnapshot.objects.create(
            scheme=intelligence_scheme,
            as_of=self.today - timedelta(days=1),
            total_holdings=1,
        )
        MFHolding.objects.create(
            snapshot=snapshot,
            isin="INE000A01001",
            name="Example Listed Company",
            industry="Banks",
            holding_type="EQUITY",
            holding_pct=Decimal("4.2500"),
            quantity=Decimal("100.0000"),
            status="UNCHANGED",
        )

        client = APIClient()
        client.force_authenticate(user)
        response = client.get(f"/api/mutual-funds/underlying/{scheme.id}/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["portfolio_date"], snapshot.as_of)
        self.assertEqual(len(response.data["results"]), 1)
        self.assertEqual(response.data["results"][0]["source"], "MF_INTELLIGENCE")
        self.assertEqual(response.data["results"][0]["percentage_of_nav"], Decimal("4.2500"))
        self.assertIsNone(response.data["results"][0]["market_value"])
