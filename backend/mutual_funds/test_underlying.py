from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase

from mutual_funds.models import MutualFundScheme, MutualFundUnderlying
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
