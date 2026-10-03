from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone
from investments.models import Asset, AssetCategory, Holding
from portfolio_news.constants import AlertSourceType, HoldingType
from portfolio_news.models import PortfolioNewsAlert
from watchlist.models import InvestmentProduct, ProductType, WatchListEntry
from .models import Filing, FilingSeverity
from .providers.base import NormalizedFiling
from .providers.mock import MockFilingProvider
from .services.classifier import classify
from .services.material_change import detect_material_change
from .services.pipeline import ingest_exchange_filings


class FilingClassifierTests(TestCase):
    def test_contextual_fraud(self):
        self.assertEqual(classify("Fraud risk identified").event_type, "OTHER")
        self.assertEqual(classify("Fraud detected by the company").event_type, "FRAUD")
        self.assertEqual(classify("Alleged fraud under investigation").event_type, "FRAUD_ALLEGATION")

    def test_required_events(self):
        cases = {
            "Auditor resignation": "AUDITOR_RESIGNATION",
            "Payment default on loan": "DEFAULT",
            "Corporate insolvency proceedings": "INSOLVENCY",
            "SEBI penalty order": "REGULATORY_ACTION",
            "Credit rating downgraded": "RATING_DOWNGRADE",
            "Promoter pledge increased": "PROMOTER_PLEDGE",
            "CFO resignation": "CFO_RESIGNATION",
            "Material litigation disclosed": "MATERIAL_LITIGATION",
            "Interim dividend declared": "DIVIDEND",
            "Board meeting intimation": "BOARD_MEETING",
        }
        for subject, event in cases.items():
            self.assertEqual(classify(subject).event_type, event)

    def test_required_event_categories(self):
        cases = {
            "Bonus shares": "BONUS",
            "Stock split": "STOCK_SPLIT",
            "Preferential issue": "PREFERENTIAL_ISSUE",
            "Major fund raising": "MAJOR_FUND_RAISING",
            "Promoter pledge released": "PROMOTER_PLEDGE_RELEASE",
            "CEO resignation": "CEO_RESIGNATION",
            "CFO resignation": "CFO_RESIGNATION",
            "Director resignation": "DIRECTOR_RESIGNATION",
            "Material order win": "MATERIAL_ORDER",
            "Major contract": "MATERIAL_CONTRACT",
            "Change in shareholding": "CHANGE_IN_SHAREHOLDING",
            "Investor presentation": "INVESTOR_PRESENTATION",
            "Earnings call": "EARNINGS_CALL",
        }
        for subject, event in cases.items():
            self.assertEqual(classify(subject).event_type, event)

    def test_combined_signals_raise_severity(self):
        result = classify(
            "Profit falls and guidance cut",
            "Quarterly results show lower profit and management reduced guidance.",
        )
        self.assertEqual(result.severity, FilingSeverity.HIGH)

    def test_material_change_detection_is_conservative(self):
        self.assertEqual(
            detect_material_change(
                "Guidance outlook expected at 12-14%",
                "Guidance outlook revised to 7-9%",
            ),
            "Guidance changed: 12-14% → 7-9%",
        )
        self.assertIsNone(
            detect_material_change(
                "Management discussed guidance",
                "Management discussed guidance again",
            )
        )

    def test_investigation_plus_management_exit_is_high(self):
        result = classify(
            "Investigation after CFO resignation",
            "Regulatory investigation is ongoing.",
        )
        self.assertEqual(result.severity, FilingSeverity.HIGH)

    def test_severity_levels(self):
        self.assertEqual(classify("Dividend declared").severity, FilingSeverity.MEDIUM)
        self.assertEqual(classify("Buyback").severity, FilingSeverity.MEDIUM)
        self.assertEqual(classify("Promoter pledge").severity, FilingSeverity.MEDIUM)
        self.assertEqual(classify("SEBI action").severity, FilingSeverity.HIGH)
        self.assertEqual(classify("Fraud detected").severity, FilingSeverity.CRITICAL)


class FilingPipelineTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="filing-user", password="x")
        self.other = User.objects.create_user(username="other-user", password="x")
        self.asset = Asset.objects.create(
            owner=self.user,
            name="Example Industries Limited",
            category=AssetCategory.STOCK,
            symbol="EXAMPLE",
            isin="INE123456789",
        )
        Holding.objects.create(owner=self.user, asset=self.asset, quantity=10, current_value=1000)

    def item(self, subject="Auditor resignation", external_id="F-1"):
        return NormalizedFiling(
            "NSE",
            "Example Industries Limited",
            "EXAMPLE",
            "INE123456789",
            "123456",
            "Corporate Announcement",
            subject,
            subject,
            "https://example.com/filing/1",
            "https://example.com/feed",
            external_id,
            timezone.now(),
        )

    def ingest(self, item=None):
        return ingest_exchange_filings(
            providers={"NSE": MockFilingProvider([item or self.item()])},
            exchanges=["NSE"],
            hours=24,
        )

    def test_portfolio_match_creates_existing_alert_type(self):
        stats = self.ingest()
        self.assertEqual(stats["provider_failures"], 0)
        self.assertEqual(stats["filings_fetched"], 1)
        self.assertEqual(stats["filings_stored"], 1)
        self.assertEqual(stats["matched"], 1)
        self.assertEqual(stats["alerts_created"], 1)

        alert = PortfolioNewsAlert.objects.get(user=self.user)
        self.assertEqual(alert.source_type, AlertSourceType.CORPORATE_FILING)
        self.assertEqual(alert.holding_type, HoldingType.EQUITY)
        self.assertEqual(alert.filing.severity, FilingSeverity.HIGH)
        self.assertFalse(PortfolioNewsAlert.objects.filter(user=self.other).exists())

    def test_duplicate_external_id_is_idempotent(self):
        first = self.ingest()
        second = self.ingest()

        self.assertEqual(first["provider_failures"], 0)
        self.assertEqual(second["provider_failures"], 0)
        self.assertEqual(second["duplicates"], 1)
        self.assertEqual(Filing.objects.count(), 1)
        self.assertEqual(PortfolioNewsAlert.objects.count(), 1)

    def test_content_hash_fallback_is_idempotent(self):
        first = self.ingest(self.item(external_id=""))
        second = self.ingest(self.item(external_id=""))

        self.assertEqual(first["provider_failures"], 0)
        self.assertEqual(second["provider_failures"], 0)
        self.assertEqual(second["duplicates"], 1)
        self.assertEqual(Filing.objects.count(), 1)

    def test_watchlist_match(self):
        product = InvestmentProduct.objects.create(
            product_type=ProductType.MUTUAL_FUND,
            name="Example Industries Limited",
            isin="INE123456789",
            external_identifier="EXAMPLE",
            identity_key="example-test",
        )
        WatchListEntry.objects.create(user=self.user, product=product)

        stats = self.ingest()

        self.assertEqual(stats["provider_failures"], 0)
        self.assertEqual(stats["alerts_created"], 2)
        self.assertTrue(
            PortfolioNewsAlert.objects.filter(
                holding_type=HoldingType.WATCHLIST
            ).exists()
        )

    def test_dry_run_creates_no_rows(self):
        stats = ingest_exchange_filings(
            providers={"NSE": MockFilingProvider([self.item()])},
            exchanges=["NSE"],
            hours=24,
            dry_run=True,
        )

        self.assertEqual(stats["provider_failures"], 0)
        self.assertEqual(stats["filings_fetched"], 1)
        self.assertEqual(stats["filings_stored"], 0)
        self.assertEqual(Filing.objects.count(), 0)
        self.assertEqual(PortfolioNewsAlert.objects.count(), 0)

    def test_provider_failure_does_not_abort_other_exchange(self):
        class Broken:
            def fetch_filings(self, since):
                raise RuntimeError("boom")

        stats = ingest_exchange_filings(
            providers={"NSE": Broken(), "BSE": MockFilingProvider([])},
            exchanges=["NSE", "BSE"],
            hours=24,
        )

        self.assertEqual(stats["provider_failures"], 1)

    def test_cross_source_same_event_creates_one_alert_and_two_sources(self):
        first = self.item(
            subject="Example Industries approves acquisition of ABC",
            external_id="NSE-ACQ-1",
        )
        second = NormalizedFiling(
            "BSE",
            "Example Industries Limited",
            "EXAMPLE",
            "INE123456789",
            "123456",
            "Corporate Announcement",
            "Example Industries to acquire ABC",
            "Example Industries to acquire ABC",
            first.filing_url,
            "https://bse.example/feed",
            "BSE-ACQ-1",
            timezone.now(),
        )

        first_stats = ingest_exchange_filings(
            providers={"NSE": MockFilingProvider([first])},
            exchanges=["NSE"],
            hours=24,
        )
        second_stats = ingest_exchange_filings(
            providers={"BSE": MockFilingProvider([second])},
            exchanges=["BSE"],
            hours=24,
        )

        self.assertEqual(first_stats["alerts_created"], 1)
        self.assertEqual(second_stats["alerts_created"], 0)
        self.assertEqual(PortfolioNewsAlert.objects.count(), 1)
        article = PortfolioNewsAlert.objects.get().article
        self.assertEqual(article.source_count, 2)

    def test_alert_isolation_in_api_queryset(self):
        stats = self.ingest()
        self.assertEqual(stats["provider_failures"], 0)
        self.assertEqual(PortfolioNewsAlert.objects.filter(user=self.other).count(), 0)
