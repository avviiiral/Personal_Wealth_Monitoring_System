from datetime import date
from decimal import Decimal
from unittest.mock import Mock, patch

import pandas as pd
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
        rows = OfficialMutualFundUnderlyingService._parse_dataframe(frame, date(2026, 8, 31))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["isin"], "INE040A01034")
        self.assertEqual(rows[0]["percentage_of_nav"], Decimal("8.20"))

    def test_discovery_uses_amc_name_without_amc_url_mapping(self):
        self.scheme.amc_name = "Example Asset Management Company Limited"
        self.scheme.scheme_name = "Example Growth Fund - Direct Plan - Growth"
        self.scheme.scheme_code = "123456"
        self.scheme.isin_growth = "INF123456789"
        self.scheme.save()

        page_url = "https://exampleamc.com/mutual-funds/example-growth-fund"
        html = """
            <html>
              <title>Example Growth Fund portfolio disclosure</title>
              <body>
                Example Growth Fund - Direct Plan - Growth
                ISIN INF123456789
                Portfolio as on 31 August 2026
              </body>
            </html>
        """
        response = Mock(text=html, content=html.encode(), status_code=200)
        response.raise_for_status.return_value = None

        with patch.object(MutualFundUnderlyingService, "_search_urls", return_value=[page_url]), \
             patch.object(MutualFundUnderlyingService, "_fetch", return_value=response):
            pages = MutualFundUnderlyingService._search_official_pages(self.scheme)

        self.assertEqual(len(pages), 1)
        self.assertEqual(pages[0][0], page_url)
        self.assertNotIn("OFFICIAL_AMC_DISCLOSURE_URLS", MutualFundUnderlyingService.__dict__)

    def test_discovery_finds_dynamic_official_page_and_download(self):
        page_url = "https://exampleamc.com/disclosures"
        file_url = "https://exampleamc.com/files/example-growth-fund-31-Aug-2026.xlsx"
        html = f"""
            <a href=\"{file_url}\">Example Growth Fund portfolio 31 August 2026</a>
            Example Growth Fund - Direct Plan - Growth
            INF000000001
        """
        response = Mock(text=html, content=html.encode(), status_code=200)
        response.raise_for_status.return_value = None

        with patch.object(MutualFundUnderlyingService, "_fetch", return_value=response), \
             patch.object(MutualFundUnderlyingService, "_search_official_pages", return_value=[(page_url, html)]):
            documents = MutualFundUnderlyingService.discover_documents(self.scheme)

        self.assertIn(page_url, documents)
        self.assertIn(file_url, documents)

    def test_html_portfolio_page_can_be_imported(self):
        html = """
        <table>
          <tr>
            <th>Name of the Instrument</th>
            <th>ISIN</th>
            <th>Industry</th>
            <th>Quantity</th>
            <th>Market Value(Rs.in Lakhs)</th>
            <th>% to NAV</th>
          </tr>
          <tr>
            <td>HDFC Bank Limited</td>
            <td>INE040A01034</td>
            <td>Banks</td>
            <td>100</td>
            <td>1250.50</td>
            <td>8.20</td>
          </tr>
        </table>
        """
        result = OfficialMutualFundUnderlyingService.import_document(
            self.scheme,
            html.encode(),
            "portfolio.html",
            "https://exampleamc.com/portfolio",
            fallback_date=date(2026, 8, 31),
        )
        self.assertEqual(result["status"], "imported")
        self.assertEqual(MutualFundUnderlying.objects.count(), 1)
        self.assertEqual(MutualFundUnderlying.objects.first().sector, "Banks")

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
