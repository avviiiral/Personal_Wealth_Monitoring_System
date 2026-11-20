from datetime import date
from decimal import Decimal
from unittest.mock import Mock, patch

import requests
from django.core.cache import cache
from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework.test import APIClient

from watchlist.models import InvestmentProduct, MutualFundProduct, PerformanceSnapshot, ProductType

from fund_intelligence.services.indianapi import IndianApiClient, IndianApiError, IndianApiNotConfigured
from fund_intelligence.services.live_matching import flatten_fund_records, match_fund_records, normalize_fund_name
from fund_intelligence.services.snapshot import build_fund_intelligence_snapshot


class FakeClient:
    def __init__(self, payload=None, error=None, configured=True):
        self.payload = payload
        self.error = error
        self.is_configured = configured
        self.calls = 0

    def mutual_funds(self):
        self.calls += 1
        if self.error:
            raise self.error
        return self.payload


def _make_fund(name, *, plan="Regular Plan", option="Growth Option", category="Equity Scheme - Multi Cap Fund",
               returns=None, aum=None, expense_ratio=None):
    product = InvestmentProduct.objects.create(
        product_type=ProductType.MUTUAL_FUND,
        name=name,
        provider=name.split()[0],
        category=category,
        identity_key=f"mf:{name}",
    )
    MutualFundProduct.objects.create(
        product=product,
        plan=plan,
        option=option,
        fund_manager="Test Manager",
        aum=aum,
        expense_ratio=expense_ratio,
        exit_load="1% if redeemed within 1 year",
    )
    if returns is not None:
        PerformanceSnapshot.objects.create(
            product=product,
            date=date(2026, 9, 30),
            source="AMFI",
            aum=aum,
            **returns,
        )
    return product


class FlattenAndMatchTests(TestCase):
    def test_flatten_collects_fund_records_from_any_depth(self):
        payload = {
            "Equity": {
                "Multi Cap": [{"fund_name": "A Multi Cap Fund"}, {"fund_name": "B Multi Cap Fund"}],
                "Large Cap": {"group": [{"fund_name": "C Large Cap Fund"}]},
            },
            "meta": "ignored",
        }
        names = sorted(record["fund_name"] for record in flatten_fund_records(payload))
        self.assertEqual(names, ["A Multi Cap Fund", "B Multi Cap Fund", "C Large Cap Fund"])

    def test_normalization_expands_sl_and_drops_plan_words(self):
        self.assertEqual(
            normalize_fund_name("Aditya Birla SL Multi-Cap Fund-Reg(G)"),
            normalize_fund_name("Aditya Birla Sun Life Multi Cap Fund - Regular Plan - Growth Option"),
        )

    def test_match_requires_clear_best_record(self):
        records = [
            {"fund_name": "HSBC Multi Cap Fund-Reg(G)", "latest_nav": "100"},
            {"fund_name": "HSBC Multi Cap Fund-Dir(G)", "latest_nav": "101"},
            {"fund_name": "Unrelated Bank Fund"},
        ]
        matches, unmatched = match_fund_records({1: "HSBC Multi Cap Fund - Regular Plan - Growth"}, records)
        self.assertEqual(matches[1]["record"]["latest_nav"], "100")
        self.assertEqual(unmatched, [])

        matches, unmatched = match_fund_records({2: "Quant Flexi Cap Fund"}, records)
        self.assertEqual(matches, {})
        self.assertEqual(unmatched, ["Quant Flexi Cap Fund"])


class IndianApiClientTests(TestCase):
    def test_missing_key_raises_without_network_call(self):
        session = Mock()
        client = IndianApiClient(api_key="", base_url="https://example.test", session=session)
        with self.assertRaises(IndianApiNotConfigured):
            client.mutual_funds()
        session.get.assert_not_called()

    def test_key_is_sent_in_header_and_not_in_url(self):
        response = Mock(status_code=200)
        response.json.return_value = [{"id": "1", "schemeName": "X"}]
        session = Mock()
        session.get.return_value = response
        client = IndianApiClient(api_key="secret-key", base_url="https://example.test/", session=session)

        self.assertEqual(client.mutual_fund_search("hdfc")[0]["id"], "1")
        args, kwargs = session.get.call_args
        self.assertEqual(args[0], "https://example.test/mutual_fund_search")
        self.assertEqual(kwargs["headers"]["X-Api-Key"], "secret-key")
        self.assertNotIn("secret-key", args[0])

    def test_transport_error_does_not_leak_request_details(self):
        session = Mock()
        session.get.side_effect = requests.ConnectionError("https://example.test?api=secret-key")
        client = IndianApiClient(api_key="secret-key", base_url="https://example.test", session=session)
        with self.assertRaises(IndianApiError) as ctx:
            client.mutual_funds()
        self.assertNotIn("secret-key", str(ctx.exception))


@override_settings(
    CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "fi-tests"}}
)
class FundIntelligenceSnapshotTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(username="fi-user", password="pw")
        self.api = APIClient()
        self.api.force_authenticate(self.user)

        self.hsbc = _make_fund(
            "HSBC Multi Cap Fund - Regular Plan - Growth",
            returns={"return_1m": Decimal("1.5"), "return_1y": Decimal("10.0"), "return_3y": Decimal("20.0"),
                     "cagr": Decimal("18.0")},
            aum=Decimal("5000.00"),
            expense_ratio=Decimal("1.7"),
        )
        self.axis = _make_fund(
            "Axis Multicap Fund - Regular Plan - Growth",
            returns={"return_1m": Decimal("0.5"), "return_1y": Decimal("6.0"), "return_3y": Decimal("14.0")},
            aum=Decimal("3000.00"),
        )
        # Excluded: direct plan, IDCW option, and a non-multi-cap category.
        _make_fund("HSBC Multi Cap Fund - Direct Plan - Growth", plan="Direct Plan")
        _make_fund("HSBC Multi Cap Fund - Regular Plan - IDCW", option="IDCW")
        _make_fund("Some Flexi Cap - Regular Plan - Growth", category="Equity Scheme - Flexi Cap Fund")

    def test_universe_excludes_direct_idcw_and_other_categories(self):
        snapshot = build_fund_intelligence_snapshot(include_live=False)
        names = [fund["name"] for fund in snapshot["funds"]]
        self.assertEqual(names, ["Axis Multicap Fund - Regular Plan - Growth", "HSBC Multi Cap Fund - Regular Plan - Growth"])
        self.assertEqual(snapshot["meta"]["fund_count"], 2)

    def test_category_average_and_relative_return_are_computed_from_stored_values(self):
        snapshot = build_fund_intelligence_snapshot(include_live=False)
        self.assertEqual(snapshot["category_average"]["1y"], 8.0)
        hsbc = next(fund for fund in snapshot["funds"] if fund["product_id"] == self.hsbc.id)
        self.assertEqual(hsbc["returns"]["1y"], 10.0)
        self.assertEqual(hsbc["vs_category"]["1y"], 2.0)
        self.assertIsNone(hsbc["returns"]["5y"])
        self.assertEqual(hsbc["expense_ratio"], 1.7)
        self.assertIsNone(hsbc["live"])

    def test_missing_key_reports_not_configured_and_keeps_stored_data(self):
        snapshot = build_fund_intelligence_snapshot(include_live=True, client=IndianApiClient(api_key="", base_url="https://x"))
        self.assertEqual(snapshot["live"]["status"], "not_configured")
        self.assertEqual(len(snapshot["funds"]), 2)

    def test_live_records_are_attached_beside_stored_values(self):
        client = FakeClient(payload={
            "Equity": {"Multi Cap": [
                {"fund_name": "HSBC Multi Cap Fund-Reg(G)", "latest_nav": "250.5", "asset_size": "5,100",
                 "star_rating": "4", "1_year_return": "11.2%"},
            ]},
        })
        snapshot = build_fund_intelligence_snapshot(include_live=True, client=client)
        hsbc = next(fund for fund in snapshot["funds"] if fund["product_id"] == self.hsbc.id)
        self.assertEqual(hsbc["returns"]["1y"], 10.0)  # stored value is not overwritten
        self.assertEqual(hsbc["live"]["latest_nav"], 250.5)
        self.assertEqual(hsbc["live"]["aum"], 5100.0)
        self.assertEqual(hsbc["live"]["returns"]["1y"], 11.2)
        self.assertEqual(snapshot["live"]["status"], "ok")
        self.assertEqual(snapshot["live"]["matched"], 1)
        self.assertEqual(snapshot["live"]["unmatched"], ["Axis Multicap Fund - Regular Plan - Growth"])

    def test_live_failure_degrades_without_failing_page(self):
        client = FakeClient(error=IndianApiError("Indian API returned HTTP 429 for /mutual_funds."))
        snapshot = build_fund_intelligence_snapshot(include_live=True, client=client)
        self.assertEqual(snapshot["live"]["status"], "error")
        self.assertIn("429", snapshot["live"]["message"])
        self.assertEqual(len(snapshot["funds"]), 2)

    def test_cache_outage_falls_back_to_direct_fetch(self):
        client = FakeClient(payload={"Equity": [{"fund_name": "HSBC Multi Cap Fund-Reg(G)"}]})
        broken_cache = Mock()
        broken_cache.get.side_effect = ConnectionError("cache down")
        broken_cache.set.side_effect = ConnectionError("cache down")
        with patch("fund_intelligence.services.snapshot.cache", broken_cache):
            snapshot = build_fund_intelligence_snapshot(include_live=True, client=client)
        self.assertEqual(snapshot["live"]["status"], "ok")
        self.assertEqual(snapshot["live"]["matched"], 1)

    def test_live_payload_is_cached(self):
        client = FakeClient(payload={"Equity": [{"fund_name": "Axis Multicap Fund-Reg(G)"}]})
        build_fund_intelligence_snapshot(include_live=True, client=client)
        build_fund_intelligence_snapshot(include_live=True, client=client)
        self.assertEqual(client.calls, 1)

    def test_endpoint_requires_authentication(self):
        anonymous = APIClient()
        response = anonymous.get(reverse("fund-intelligence-snapshot") + "?live=0")
        self.assertIn(response.status_code, (401, 403))

    def test_endpoint_returns_snapshot_without_live_call(self):
        response = self.api.get(reverse("fund-intelligence-snapshot") + "?live=0")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["live"]["status"], "disabled")
        self.assertEqual(response.data["meta"]["fund_count"], 2)
        self.assertTrue(response.data["data_coverage"]["not_available"])
