import json
from datetime import date

from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APIClient

from watchlist.management.commands.import_mutual_fund_research_report import extract_js_json
from watchlist.models import MutualFundResearchReport


class MutualFundResearchReportTests(TestCase):
    def test_extracts_json_objects_without_evaluating_javascript(self):
        source = 'const RAW = {"meta":{"category":"Multi Cap"},"funds":[{"name":"A"}]};'
        self.assertEqual(extract_js_json(source, "RAW")["meta"]["category"], "Multi Cap")

    def test_report_endpoint_returns_imported_payload(self):
        user = User.objects.create_user(username="research-report-user", password="test")
        report = MutualFundResearchReport.objects.create(
            report_key="multi-cap",
            report_date=date(2026, 9, 30),
            report_cycle="October 2026",
            source_file="Nexedge_multicap_dashboard_Oct2026.html",
            payload={"RAW": {"funds": [{"name": "Fund A"}]}, "P2P": {}, "ATTR": {}},
        )
        client = APIClient()
        client.force_authenticate(user)
        response = client.get("/api/mutual-fund-analytics/research-report/")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["available"])
        self.assertEqual(response.data["id"], report.id)
        self.assertEqual(response.data["payload"]["RAW"]["funds"][0]["name"], "Fund A")

    def test_report_endpoint_can_return_a_selected_universe(self):
        user = User.objects.create_user(username="research-selection-user", password="test")
        older = MutualFundResearchReport.objects.create(
            report_key="large-cap",
            report_date=date(2026, 8, 31),
            report_cycle="August 2026",
            source_file="large-cap.html",
            payload={"RAW": {"meta": {"category": "Large Cap"}, "funds": [{"name": "Fund L"}]}, "P2P": {}, "ATTR": {}},
        )
        newer = MutualFundResearchReport.objects.create(
            report_key="multi-cap",
            report_date=date(2026, 9, 30),
            report_cycle="October 2026",
            source_file="multi-cap.html",
            payload={"RAW": {"meta": {"category": "Multi Cap"}, "funds": [{"name": "Fund M"}]}, "P2P": {}, "ATTR": {}},
        )
        client = APIClient()
        client.force_authenticate(user)

        response = client.get("/api/mutual-fund-analytics/research-report/", {"id": older.id})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["id"], older.id)
        self.assertEqual(response.data["payload"]["RAW"]["meta"]["category"], "Large Cap")

    def test_research_universe_list_returns_selectable_imports(self):
        user = User.objects.create_user(username="research-universe-list-user", password="test")
        older = MutualFundResearchReport.objects.create(
            report_key="large-cap",
            report_date=date(2026, 8, 31),
            report_cycle="August 2026",
            source_file="large-cap.html",
            payload={"RAW": {"meta": {"category": "Large Cap"}, "funds": [{"name": "Fund L"}]}, "P2P": {}, "ATTR": {}},
        )
        newer = MutualFundResearchReport.objects.create(
            report_key="multi-cap",
            report_date=date(2026, 9, 30),
            report_cycle="October 2026",
            source_file="multi-cap.html",
            payload={"RAW": {"meta": {"category": "Multi Cap"}, "funds": [{"name": "Fund M"}, {"name": "Fund N"}]}, "P2P": {}, "ATTR": {}},
        )
        MutualFundResearchReport.objects.create(
            report_key="indianapi-india",
            report_date=date(2026, 10, 31),
            payload={"funds": [{"name": "Provider snapshot"}]},
        )
        client = APIClient()
        client.force_authenticate(user)

        response = client.get("/api/mutual-fund-analytics/research-universes/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 2)
        self.assertEqual([row["id"] for row in response.data["results"]], [newer.id, older.id])
        self.assertEqual(response.data["results"][0]["category"], "Multi Cap")
        self.assertEqual(response.data["results"][0]["fund_count"], 2)

    def test_report_endpoint_requires_authentication(self):
        response = APIClient().get("/api/mutual-fund-analytics/research-report/")
        self.assertEqual(response.status_code, 403)
