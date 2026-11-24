from unittest.mock import Mock

from django.contrib.auth.models import User
from django.test import SimpleTestCase, TestCase
from django.urls import resolve
from rest_framework.test import APIRequestFactory, force_authenticate

from mutual_funds.charliealpha_api import charliealpha_provider_resources
from mutual_funds.master_models import MFProviderResourceSnapshot, MFSourceRun
from mutual_funds.services.charliealpha_extended import (
    CharlieAlphaExtendedService,
    _resource_records,
)


class CharlieAlphaExtendedParserTests(SimpleTestCase):
    def test_compact_table_and_date_value_arrays_become_records(self):
        compact = {
            "labels": ["fund_id", "fund_name", "currency"],
            "data": [[1, "Example IFSC Fund", "USD"]],
        }
        self.assertEqual(
            _resource_records(compact),
            [{"fund_id": 1, "fund_name": "Example IFSC Fund", "currency": "USD"}],
        )
        history = {"dates": ["2026-11-20"], "nav": ["101.25"]}
        self.assertEqual(
            _resource_records(history),
            [{"date": "2026-11-20", "nav": "101.25"}],
        )

    def test_gift_plan_keyed_series_preserve_plan_identity(self):
        payload = {
            "Direct": {"dates": ["2026-11-20"], "nav": ["101.25"]},
            "Class I": {"dates": ["2026-11-20"], "nav": ["99.75"]},
        }
        self.assertEqual(
            _resource_records(payload),
            [
                {"plan": "Direct", "date": "2026-11-20", "nav": "101.25"},
                {"plan": "Class I", "date": "2026-11-20", "nav": "99.75"},
            ],
        )

    def test_gift_latest_nav_keyed_by_plan_preserves_scalar_plan_rows(self):
        payload = {
            "Direct": {"nav": "10.25", "previous_nav": "10.10", "date": "2026-11-21"},
            "Class I": {"nav": "10.50", "previous_nav": "10.30", "date": "2026-11-21"},
        }
        rows = _resource_records(payload)
        self.assertEqual([row["plan"] for row in rows], ["Direct", "Class I"])
        self.assertEqual([row["nav"] for row in rows], ["10.25", "10.50"])


class CharlieAlphaExtendedPersistenceTests(TestCase):
    def test_nps_directory_and_all_three_scheme_endpoints_are_persisted(self):
        client = Mock()
        client.get.side_effect = [
            {"schemes": [{"scheme_code": "SM001001", "scheme_name": "NPS Equity Scheme"}]},
            {"scheme_code": "SM001001", "nav": "42.5", "date": "2026-11-21"},
            {"dates": ["2026-11-20", "2026-11-21"], "nav": ["41.9", "42.5"]},
            {"horizon": "3y", "cagr": "12.5", "sharpe": "0.8"},
        ]

        result = CharlieAlphaExtendedService(client=client).sync_nps(limit=1, horizon="3y")

        self.assertEqual(result["schemes_discovered"], 1)
        self.assertEqual(result["snapshots_saved"], 4)
        self.assertEqual(
            [call.args[0] for call in client.get.call_args_list],
            [
                "nps/schemes",
                "nps/lnav/SM001001",
                "nps/hnav/SM001001",
                "nps/metrics/SM001001",
            ],
        )
        self.assertEqual(client.get.call_args_list[3].args[1], {"horizon": "3y"})
        snapshots = list(MFProviderResourceSnapshot.objects.filter(family="NPS").order_by("endpoint"))
        self.assertEqual(len(snapshots), 4)
        history = MFProviderResourceSnapshot.objects.get(endpoint="nps/hnav/SM001001")
        self.assertEqual(history.record_count, 2)
        self.assertEqual(history.records[1]["nav"], "42.5")
        self.assertTrue(MFSourceRun.objects.filter(
            source="CHARLIEALPHA_API",
            metadata__provider_family="NPS",
            status=MFSourceRun.Status.SUCCESS,
        ).exists())

    def test_sif_directory_sifs_envelope_is_normalized_and_identifiers_are_discovered(self):
        client = Mock()
        client.get.side_effect = [
            {
                "sifs": [
                    {"scheme_code": "SIF-1", "scheme_name": "Example SIF Growth"},
                    {"scheme_code": "SIF-2", "scheme_name": "Example SIF IDCW"},
                ]
            },
            {"fields": ["scheme_code", "nav"], "data": [["SIF-1", "11.42"]]},
            {"nav": "11.42", "date": "2026-11-23"},
            {"dates": ["2026-11-22", "2026-11-23"], "nav": ["11.30", "11.42"]},
        ]

        result = CharlieAlphaExtendedService(client=client).sync_sif(limit=1)

        self.assertEqual(result["schemes_discovered"], 2)
        self.assertEqual(result["schemes_requested"], 1)
        directory = MFProviderResourceSnapshot.objects.get(
            family="SIF", endpoint="sif/schemes",
        )
        self.assertEqual(directory.record_count, 2)
        self.assertEqual(
            [row["scheme_code"] for row in directory.records],
            ["SIF-1", "SIF-2"],
        )
        self.assertEqual(
            [call.args[0] for call in client.get.call_args_list],
            [
                "sif/schemes",
                "sif/lnavall",
                "sif/lnav/SIF-1",
                "sif/hnav/SIF-1",
            ],
        )

    def test_sif_and_gift_keep_their_own_identifiers_and_request_contracts(self):
        sif_client = Mock()
        sif_client.get.side_effect = [
            {"fields": ["scheme_id", "scheme_name"], "data": [[7, "SIF Example"]]},
            {"fields": ["scheme_code", "nav"], "data": [[7, "100"]]},
            {"fields": ["code", "name", "category"], "data": [["7", "SIF Example", "Equity"]]},
            {"nav": "100", "date": "2026-11-21"},
            {"dates": ["2026-11-20"], "nav": ["99.5"]},
        ]
        sif_result = CharlieAlphaExtendedService(client=sif_client).sync_sif(limit=1, search_query="long short")
        self.assertEqual(sif_result["schemes_requested"], 1)
        self.assertEqual(sif_client.get.call_args_list[2].args, ("sif/search", {"q": "long short"}))
        self.assertEqual(sif_client.get.call_args_list[3].args[0], "sif/lnav/7")
        self.assertEqual(sif_client.get.call_args_list[4].args[0], "sif/hnav/7")
        self.assertEqual(MFProviderResourceSnapshot.objects.filter(family="SIF").count(), 5)

        gift_client = Mock()
        gift_client.get.side_effect = [
            {"labels": ["fund_id", "fund_name", "isin"], "data": [[3, "GIFT Example", "INIFC2603016"]]},
            {"fields": ["fund_id", "nav"], "data": [[3, "10"]]},
            {"results": [{"fund_id": 3, "fund_name": "GIFT Example"}]},
            {"fund_id": 3, "nav": "10.1", "date": "2026-11-21"},
            {"Direct": {"dates": ["2026-11-20"], "nav": ["10"]}},
        ]
        gift_result = CharlieAlphaExtendedService(client=gift_client).sync_gift(
            limit=1, search_query="global equity", plan="Direct", from_date="2026-11-01", to_date="2026-11-21",
        )
        self.assertEqual(gift_result["schemes_requested"], 1)
        self.assertEqual(gift_client.get.call_args_list[0].args[0], "gift/schemes")
        self.assertEqual(gift_client.get.call_args_list[0].args[1], {"compact": "false"})
        self.assertEqual(
            gift_client.get.call_args_list[2].args,
            ("gift/search", {"q": "global equity", "compact": "false"}),
        )
        self.assertEqual(
            gift_client.get.call_args_list[4].args[1],
            {"plan": "Direct", "start_date": "2026-11-01", "end_date": "2026-11-21"},
        )
        self.assertEqual(MFProviderResourceSnapshot.objects.filter(family="GIFT").count(), 5)

    def test_gift_history_accepts_one_sided_optional_date_boundaries(self):
        client = Mock()
        client.get.side_effect = [
            {"labels": ["fund_id", "fund_name"], "data": [[3, "GIFT Example"]]},
            {"fields": ["fund_id", "nav"], "data": [[3, "10"]]},
            {"fund_id": 3, "nav": "10.1", "date": "2026-11-21"},
            {"Direct": {"dates": ["2026-11-20"], "nav": ["10"]}},
        ]

        CharlieAlphaExtendedService(client=client).sync_gift(
            limit=1, from_date="2026-11-01",
        )

        self.assertEqual(
            client.get.call_args_list[3].args[1],
            {"start_date": "2026-11-01"},
        )

    def test_instrument_search_details_and_corporate_actions_are_persisted(self):
        client = Mock()
        client.get.side_effect = [
            {"data": [{"isin": "INE002A01018", "name": "Example Ltd", "type": "EQUITY"}]},
            {"isin": "INE002A01018", "issuer": "Example Ltd", "industry": "Banks"},
            {"count": 1, "data": [{"action_type": "BONUS", "ratio": "1:1"}]},
        ]
        service = CharlieAlphaExtendedService(client=client)

        service.search_instruments("Example", instrument_type="EQUITY", limit=25)
        service.instrument_details("INE002A01018")
        service.corporate_actions(
            code="INE002A01018",
            status_filter="upcoming",
            action_type="BONUS",
            from_date="2026-01-01",
            to_date="2026-12-31",
            limit=20,
        )

        self.assertEqual(
            client.get.call_args_list[0].args,
            ("instruments/search", {"q": "Example", "limit": 25, "type": "EQUITY"}),
        )
        self.assertEqual(client.get.call_args_list[0].kwargs, {})
        self.assertEqual(client.get.call_args_list[1].args[0], "instruments/INE002A01018")
        self.assertEqual(
            client.get.call_args_list[2].args,
            ("instruments/corp-act", {
                "status": "upcoming",
                "action_type": "BONUS",
                "limit": 20,
                "code": "INE002A01018",
                "from_date": "2026-01-01",
                "to_date": "2026-12-31",
            }),
        )
        self.assertEqual(MFProviderResourceSnapshot.objects.filter(family="INSTRUMENTS").count(), 2)
        self.assertEqual(MFProviderResourceSnapshot.objects.filter(family="CORPORATE_ACTIONS").count(), 1)

    def test_empty_search_result_is_not_counted_as_a_record(self):
        client = Mock()
        client.get.return_value = {"query": "no matches", "count": 0, "data": []}

        CharlieAlphaExtendedService(client=client).search_instruments("no matches")

        snapshot = MFProviderResourceSnapshot.objects.get(family="INSTRUMENTS")
        self.assertEqual(snapshot.record_count, 0)
        self.assertEqual(snapshot.records, [])
        self.assertEqual(snapshot.payload["count"], 0)

    def test_failures_are_audited_without_fabricating_empty_success_data(self):
        client = Mock()
        client.get.side_effect = RuntimeError("provider unavailable")

        snapshot = CharlieAlphaExtendedService(client=client)._get(
            "NPS", "nps/schemes",
        )

        self.assertFalse(snapshot.success)
        self.assertEqual(snapshot.records, [])
        self.assertIn("provider unavailable", snapshot.error)
        run = MFSourceRun.objects.get(metadata__resource_snapshot_id=snapshot.id)
        self.assertEqual(run.status, MFSourceRun.Status.FAILED)

    def test_dry_run_fetches_but_does_not_write_resource_or_audit_rows(self):
        client = Mock()
        client.get.return_value = {"schemes": [{"scheme_code": "SM001001"}]}

        result = CharlieAlphaExtendedService(client=client, dry_run=True).sync_nps(limit=0)

        self.assertEqual(result["schemes_discovered"], 1)
        self.assertFalse(MFProviderResourceSnapshot.objects.exists())
        self.assertFalse(MFSourceRun.objects.filter(source="CHARLIEALPHA_API").exists())

    def test_provider_resource_api_is_authenticated_and_returns_family_snapshots(self):
        user = User.objects.create_user(username="charliealpha-provider-test", password="test-password")
        snapshot = MFProviderResourceSnapshot.objects.create(
            family="NPS",
            endpoint="nps/hnav/SM001001",
            identifier="SM001001",
            request_params={"from": "2026-01-01"},
            payload={"dates": ["2026-11-20"], "nav": ["42.5"]},
            records=[{"date": "2026-11-20", "nav": "42.5"}],
            record_count=1,
            success=True,
        )
        self.assertEqual(
            resolve("/api/mutual-fund-analytics/charliealpha/provider-data/").url_name,
            "charliealpha-provider-resources",
        )

        request = APIRequestFactory().get(
            "/api/mutual-fund-analytics/charliealpha/provider-data/?family=NPS&limit=5"
        )
        force_authenticate(request, user=user)
        response = charliealpha_provider_resources(request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["family_counts"]["NPS"], 1)
        self.assertEqual(response.data["results"][0]["id"], snapshot.id)
        self.assertEqual(response.data["results"][0]["records"][0]["nav"], "42.5")

    def test_provider_resource_api_rejects_unknown_family(self):
        user = User.objects.create_user(username="charliealpha-provider-invalid", password="test-password")
        request = APIRequestFactory().get(
            "/api/mutual-fund-analytics/charliealpha/provider-data/?family=UNSUPPORTED"
        )
        force_authenticate(request, user=user)
        response = charliealpha_provider_resources(request)
        self.assertEqual(response.status_code, 400)
