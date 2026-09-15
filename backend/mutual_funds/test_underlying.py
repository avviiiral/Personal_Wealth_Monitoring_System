from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase

from investments.models import Asset, AssetCategory, Holding
from mutual_funds.models import MutualFundScheme, MutualFundUnderlying
from mutual_funds.services.official_underlying import OfficialMutualFundUnderlyingService
from mutual_funds.services.underlying import MutualFundUnderlyingService


class MutualFundUnderlyingServiceTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="mf-underlying-test", password="test")
        self.scheme = MutualFundScheme.objects.create(
            owner=self.user,
            scheme_name="Test Equity Fund - Direct Plan - Growth",
            amc_name="Test AMC",
            scheme_code="999999",
            isin_growth="INF000000001",
        )

    def test_normalized_security_key_prefers_isin(self):
        key = MutualFundUnderlyingService.normalize_security_key("HDFC Bank Limited", "INE040A01034")
        self.assertEqual(key, "ISIN:INE040A01034")

    def test_import_keeps_historical_snapshots(self):
        first = MutualFundUnderlying.objects.create(
            scheme=self.scheme,
            security_name="HDFC Bank Limited",
            isin="INE040A01034",
            security_key="ISIN:INE040A01034",
            quantity=Decimal("100"),
            market_value=Decimal("1000"),
            percentage_of_nav=Decimal("8.2000"),
            portfolio_date=date(2026, 7, 31),
            source="AMFI",
        )
        second = MutualFundUnderlying.objects.create(
            scheme=self.scheme,
            security_name="HDFC Bank Limited",
            isin="INE040A01034",
            security_key="ISIN:INE040A01034",
            quantity=Decimal("120"),
            market_value=Decimal("1250"),
            percentage_of_nav=Decimal("8.5000"),
            portfolio_date=date(2026, 8, 31),
            source="AMFI",
        )
        self.assertEqual(MutualFundUnderlying.objects.filter(scheme=self.scheme).count(), 2)
        self.assertNotEqual(first.portfolio_date, second.portfolio_date)

    def test_duplicate_snapshot_is_rejected(self):
        MutualFundUnderlying.objects.create(
            scheme=self.scheme,
            security_name="ICICI Bank Limited",
            isin="INE090A01021",
            security_key="ISIN:INE090A01021",
            quantity=Decimal("100"),
            market_value=Decimal("1000"),
            percentage_of_nav=Decimal("7.5000"),
            portfolio_date=date(2026, 8, 31),
            source="AMFI",
        )
        with self.assertRaises(Exception):
            MutualFundUnderlying.objects.create(
                scheme=self.scheme,
                security_name="ICICI Bank Limited",
                isin="INE090A01021",
                security_key="ISIN:INE090A01021",
                quantity=Decimal("100"),
                market_value=Decimal("1000"),
                percentage_of_nav=Decimal("7.5000"),
                portfolio_date=date(2026, 8, 31),
                source="AMFI",
            )

    def test_parse_standard_disclosure_columns(self):
        import pandas as pd

        frame = pd.DataFrame([
            {
                "Name of the Instrument": "HDFC Bank Limited",
                "ISIN": "INE040A01034",
                "Industry": "Banks",
                "Quantity": 100,
                "Market Value(Rs.in Lakhs)": 1250.50,
                "% to NAV": 8.20,
            }
        ])
        rows = MutualFundUnderlyingService._parse_dataframe(frame, date(2026, 8, 31))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["isin"], "INE040A01034")
        self.assertEqual(rows[0]["percentage_of_nav"], Decimal("8.20"))

    @patch.object(OfficialMutualFundUnderlyingService, "fetch_scheme")
    def test_fetch_all_active_uses_live_portfolio_holdings_only(self, mock_fetch):
        asset = Asset.objects.create(
            owner=self.user,
            name=self.scheme.scheme_name,
            category=AssetCategory.MUTUAL_FUND,
            isin=self.scheme.isin_growth,
        )
        Holding.objects.create(
            owner=self.user,
            asset=asset,
            quantity=Decimal("100"),
            average_cost=Decimal("10"),
            invested_value=Decimal("1000"),
            current_price=Decimal("12"),
            current_value=Decimal("1200"),
        )

        MutualFundScheme.objects.create(
            owner=self.user,
            scheme_name="Unused Fund - Direct Plan - Growth",
            amc_name="Test AMC",
            scheme_code="888888",
            isin_growth="INF000000002",
            is_active=True,
        )
        mock_fetch.return_value = {
            "status": "imported",
            "portfolio_date": date(2026, 8, 31),
            "records": 10,
        }

        result = OfficialMutualFundUnderlyingService.fetch_all_active(owner_ids=[self.user.id])

        self.assertEqual(result["schemes"], 1)
        self.assertEqual(mock_fetch.call_count, 1)
        self.assertEqual(mock_fetch.call_args.args[0].id, self.scheme.id)
