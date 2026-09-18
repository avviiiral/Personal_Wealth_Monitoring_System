from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from mutual_funds.services.official_discovery_fallback import ProductionMutualFundUnderlyingService


class OfficialDiscoveryFallbackTests(SimpleTestCase):
    def test_known_amc_fallback_pages_are_selected(self):
        hdfc = SimpleNamespace(scheme_name="HDFC Focused Fund", amc_name="HDFC Mutual Fund")
        bandhan = SimpleNamespace(scheme_name="BANDHAN Dynamic Term Fund", amc_name="Bandhan Mutual Fund")
        icici = SimpleNamespace(scheme_name="ICICI Prudential Liquid Fund -", amc_name="ICICI Prudential Mutual Fund")
        kotak = SimpleNamespace(scheme_name="Kotak Liquid Fund", amc_name="Kotak Mahindra Mutual Fund")

        assert "hdfcfund.com" in ProductionMutualFundUnderlyingService._fallback_page_urls(hdfc)[0]
        assert "bandhanmutual.com" in ProductionMutualFundUnderlyingService._fallback_page_urls(bandhan)[0]
        assert "icicipruamc.com" in ProductionMutualFundUnderlyingService._fallback_page_urls(icici)[0]
        assert "kotakmf.com" in ProductionMutualFundUnderlyingService._fallback_page_urls(kotak)[0]

    def test_embedded_excel_links_are_extracted_for_matching_scheme(self):
        scheme = SimpleNamespace(
            scheme_name="HDFC Focused Fund",
            scheme_code="118950",
            isin_growth="INF179K01VK7",
            isin_dividend=None,
            amc_name="HDFC Mutual Fund",
        )
        html = (
            '"https://files.hdfcfund.com/s3fs-public/2026-09/'
            'Monthly%20HDFC%20Focused%20Fund%20-%2031%20August%202026.xlsx"'
        )

        links = ProductionMutualFundUnderlyingService._embedded_download_links(
            html,
            "https://www.hdfcfund.com/statutory-disclosure/portfolio/monthly-portfolio",
            scheme,
        )

        self.assertEqual(len(links), 1)
        self.assertTrue(links[0].lower().endswith(".xlsx"))
        self.assertIn("focused", links[0].lower())

    @patch.object(ProductionMutualFundUnderlyingService, "_fetch")
    @patch.object(ProductionMutualFundUnderlyingService.__mro__[1], "discover_documents", return_value=[])
    def test_discovery_uses_official_fallback_page(self, _base_discovery, mock_fetch):
        scheme = SimpleNamespace(
            scheme_name="Kotak Liquid Fund",
            scheme_code="119766",
            isin_growth="INF174K01NE8",
            isin_dividend=None,
            amc_name="Kotak Mahindra Mutual Fund",
        )
        mock_fetch.return_value = SimpleNamespace(
            text="Kotak Liquid Fund Portfolio Asset Name % of net asset",
        )

        documents = ProductionMutualFundUnderlyingService.discover_documents(scheme)

        self.assertIn(
            "https://www.kotakmf.com/mutual-funds/debt-funds/kotak-liquid-fund/dir-g",
            documents,
        )
