from unittest import mock

from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APIClient

from mf_intelligence.models import MFCategory, MFScheme, MFSyncRun
from mf_intelligence.services.sync import SYNC_LOCK


class MFIntelligenceApiTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.category = MFCategory.objects.create(slug="multi-cap", name="Equity Scheme - Multi Cap Fund", asset_class="Equity")
        MFScheme.objects.create(amfi_code="100001", name="Alpha Multi Cap Fund - Regular Plan - Growth",
                                category=cls.category, plan="Regular", option="Growth")
        cls.viewer = User.objects.create_user(username="mfi-viewer", password="pw")
        cls.admin = User.objects.create_user(username="mfi-admin", password="pw")
        cls.admin.profile.role = "ADMIN"
        cls.admin.profile.save(update_fields=["role"])

    def client_for(self, user=None):
        client = APIClient()
        if user:
            client.force_authenticate(user)
        return client

    def test_reads_require_authentication(self):
        anonymous = self.client_for()
        for url in (
            "/api/mf-intelligence/categories/",
            "/api/mf-intelligence/categories/multi-cap/dashboard/",
            "/api/mf-intelligence/schemes/100001/",
            "/api/mf-intelligence/sync/status/",
        ):
            with self.subTest(url=url):
                self.assertIn(anonymous.get(url).status_code, (401, 403))

    def test_viewer_can_read(self):
        client = self.client_for(self.viewer)
        self.assertEqual(client.get("/api/mf-intelligence/categories/").status_code, 200)
        response = client.get("/api/mf-intelligence/categories/multi-cap/dashboard/?plan=direct&horizon=5y")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["plan"], "Direct")
        self.assertEqual(response.data["horizon"], "5Y")
        self.assertEqual(client.get("/api/mf-intelligence/schemes/100001/").status_code, 200)

    def test_invalid_query_values_fall_back_to_defaults(self):
        response = self.client_for(self.viewer).get("/api/mf-intelligence/categories/multi-cap/dashboard/?plan=x&horizon=7Y")
        self.assertEqual(response.data["plan"], "Regular")
        self.assertEqual(response.data["horizon"], "3Y")

    def test_missing_or_blank_query_params_use_defaults(self):
        client = self.client_for(self.viewer)
        for query in ("", "?plan=&horizon=&benchmark=", "?benchmark=%20%20"):
            with self.subTest(query=query):
                response = client.get(f"/api/mf-intelligence/categories/multi-cap/dashboard/{query}")
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.data["plan"], "Regular")
                self.assertEqual(response.data["horizon"], "3Y")
                self.assertEqual(response.data["funds"][0]["code"], "100001")

    def test_horizon_and_plan_are_case_insensitive_at_the_edges(self):
        client = self.client_for(self.viewer)
        longest = client.get("/api/mf-intelligence/categories/multi-cap/dashboard/?horizon=10y&plan=REGULAR")
        self.assertEqual((longest.data["horizon"], longest.data["plan"]), ("10Y", "Regular"))
        shortest = client.get("/api/mf-intelligence/categories/multi-cap/dashboard/?horizon=1Y&plan=Direct")
        self.assertEqual((shortest.data["horizon"], shortest.data["plan"]), ("1Y", "Direct"))
        self.assertEqual(shortest.data["funds"], [])  # no Direct plan scheme stored

    def test_unknown_resources_return_404(self):
        client = self.client_for(self.viewer)
        self.assertEqual(client.get("/api/mf-intelligence/categories/nope/dashboard/").status_code, 404)
        self.assertEqual(client.get("/api/mf-intelligence/schemes/999999/").status_code, 404)

    def test_viewer_cannot_start_sync(self):
        response = self.client_for(self.viewer).post("/api/mf-intelligence/sync/", {}, format="json")
        self.assertEqual(response.status_code, 403)
        self.assertFalse(MFSyncRun.objects.exists())

    @mock.patch("mf_intelligence.views.threading.Thread")
    def test_admin_starts_background_sync(self, thread):
        response = self.client_for(self.admin).post(
            "/api/mf-intelligence/sync/", {"category": "multi-cap", "plan": "Both"}, format="json"
        )
        try:
            self.assertEqual(response.status_code, 202)
            self.assertEqual(response.data["status"], "started")
            run = MFSyncRun.objects.get()
            self.assertEqual(run.triggered_by, self.admin)
            self.assertEqual(run.scope["plans"], ["Regular", "Direct"])
            args = thread.call_args.kwargs["args"]
            self.assertEqual(args[1:], (["multi-cap"], ["Regular", "Direct"], False))
            thread.return_value.start.assert_called_once()
        finally:
            # The mocked thread never runs, so release the lock it would release.
            if SYNC_LOCK.locked():
                SYNC_LOCK.release()

    @mock.patch("mf_intelligence.views.threading.Thread")
    def test_empty_or_null_sync_body_means_all_categories_regular_plan(self, thread):
        client = self.client_for(self.admin)
        for body in ({}, {"category": None, "plan": None, "force": None}, {"category": "", "plan": ""}):
            with self.subTest(body=body):
                try:
                    response = client.post("/api/mf-intelligence/sync/", body, format="json")
                    self.assertEqual(response.status_code, 202)
                    args = thread.call_args.kwargs["args"]
                    self.assertEqual(args[1:], (None, ["Regular"], False))
                finally:
                    if SYNC_LOCK.locked():
                        SYNC_LOCK.release()
        self.assertEqual(MFSyncRun.objects.filter(scope__categories="all").count(), 3)

    @mock.patch("mf_intelligence.views.threading.Thread")
    def test_non_object_sync_body_is_treated_as_empty(self, thread):
        try:
            response = self.client_for(self.admin).post("/api/mf-intelligence/sync/", [], format="json")
            self.assertEqual(response.status_code, 202)
        finally:
            if SYNC_LOCK.locked():
                SYNC_LOCK.release()

    def test_sync_validates_input(self):
        client = self.client_for(self.admin)
        self.assertEqual(client.post("/api/mf-intelligence/sync/", {"category": "nope"}, format="json").status_code, 400)
        self.assertEqual(client.post("/api/mf-intelligence/sync/", {"plan": "Weekly"}, format="json").status_code, 400)
        self.assertFalse(SYNC_LOCK.locked())

    def test_second_sync_reports_already_running(self):
        self.assertTrue(SYNC_LOCK.acquire(blocking=False))
        try:
            response = self.client_for(self.admin).post("/api/mf-intelligence/sync/", {}, format="json")
            self.assertEqual(response.status_code, 202)
            self.assertEqual(response.data["status"], "already_running")
            status = self.client_for(self.viewer).get("/api/mf-intelligence/sync/status/")
            self.assertTrue(status.data["running"])
        finally:
            SYNC_LOCK.release()
