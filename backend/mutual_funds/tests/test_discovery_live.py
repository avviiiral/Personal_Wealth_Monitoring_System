from unittest.mock import Mock, patch

from django.test import TestCase
from django.contrib.auth.models import User

from mutual_funds.models import MutualFundScheme
from mutual_funds.services.underlying import MutualFundUnderlyingService


class GenericDiscoveryTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="discovery-test", password="test")
        self.scheme = MutualFundScheme.objects.create(
            owner=self.user,
            scheme_name="Example Equity Fund - Direct Plan - Growth",
            amc_name="Example Asset Management Company Limited",
            scheme_code="123456",
            isin_growth="INF123456789",
        )

    def test_amfi_scheme_details_can_supply_official_amc_host(self):
        html = '''
        <a href="https://exampleamc.com/mutual-funds">Example Asset Management Company</a>
        <a href="https://other.example.com">Other</a>
        '''
        response = Mock(text=html, status_code=200)
        response.raise_for_status.return_value = None
        with patch.object(MutualFundUnderlyingService, "_fetch", return_value=response):
            domains = MutualFundUnderlyingService._discover_amc_domains_from_amfi(self.scheme)
        self.assertIn("https://exampleamc.com", domains)

    def test_search_accepts_pdf_document_from_official_amc(self):
        url = "https://exampleamc.com/files/example-equity-fund-aug-2026.pdf"
        with patch.object(MutualFundUnderlyingService, "_search_urls", return_value=[url]):
            pages = MutualFundUnderlyingService._search_official_pages(self.scheme)
        self.assertEqual(pages[0][0], url)

    def test_search_result_does_not_accept_unrelated_host(self):
        url = "https://unrelated.example/files/fund.pdf"
        with patch.object(MutualFundUnderlyingService, "_search_urls", return_value=[url]):
            pages = MutualFundUnderlyingService._search_official_pages(self.scheme)
        self.assertEqual(pages, [])
