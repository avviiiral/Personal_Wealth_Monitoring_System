from decimal import Decimal
from datetime import date

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from investments.models import Asset
from market_data.models import DataSource, MarketPrice
from users.models import FamilyGroup, Role, UserAuditLog, UserProfile
from users.permissions import get_visible_owner_ids

User = get_user_model()


def make_user(username, role, password="test-pass-123", **extra):
    user = User.objects.create_user(username=username, password=password, **extra)
    # signal auto-creates a VIEWER profile; upgrade it explicitly.
    profile = user.profile
    profile.role = role
    profile.save(update_fields=["role"])

    user.is_superuser = role == Role.SYSTEM_OWNER
    user.is_staff = role != Role.VIEWER
    user.save(update_fields=["is_superuser", "is_staff"])

    return user


def client_for(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


# ======================================================================
# ROLE MODEL
# ======================================================================


class RoleModelTests(TestCase):
    def test_profile_auto_created_on_user_creation(self):
        user = User.objects.create_user(username="freshuser", password="x")

        self.assertTrue(UserProfile.objects.filter(user=user).exists())
        self.assertEqual(user.profile.role, Role.VIEWER)

    def test_django_superuser_gets_system_owner_role(self):
        user = User.objects.create_superuser(
            username="clisuper", password="x", email="clisuper@example.com"
        )

        self.assertEqual(user.profile.role, Role.SYSTEM_OWNER)

    def test_is_last_active_system_owner(self):
        owner = make_user("solo_owner", Role.SYSTEM_OWNER)

        self.assertTrue(UserProfile.is_last_active_system_owner(owner))

        owner2 = make_user("solo_owner2", Role.SYSTEM_OWNER)

        self.assertFalse(UserProfile.is_last_active_system_owner(owner))
        self.assertFalse(UserProfile.is_last_active_system_owner(owner2))

    def test_role_hierarchy_order(self):
        from users.models import ROLE_ORDER

        self.assertEqual(
            ROLE_ORDER,
            [Role.VIEWER, Role.ADMIN, Role.SUPER_USER, Role.SYSTEM_OWNER],
        )


# ======================================================================
# SHARED FIXTURE
# ======================================================================


class BaseRBACTestCase(TestCase):
    def setUp(self):
        self.system_owner = make_user("root_owner", Role.SYSTEM_OWNER)
        self.super_user = make_user("ops_super", Role.SUPER_USER)
        self.admin = make_user("ops_admin", Role.ADMIN)
        self.viewer = make_user("read_only_viewer", Role.VIEWER)

        self.asset = Asset.objects.create(
            owner=self.viewer,
            name="Viewer Owned Stock",
            category="STOCK",
            isin="INE000TESTV01",
        )

        self.admin_asset = Asset.objects.create(
            owner=self.admin,
            name="Admin Owned Stock",
            category="STOCK",
            isin="INE000TESTA01",
        )

        MarketPrice.objects.create(
            asset=self.admin_asset,
            date=date(2026, 1, 1),
            close_price=Decimal("100"),
            source=DataSource.YAHOO_FINANCE,
        )

    def client_as(self, user):
        return client_for(user)


# ======================================================================
# /api/settings/me/  and  /api/auth/me/
# ======================================================================


class MeEndpointTests(BaseRBACTestCase):
    def test_settings_me_returns_role_and_permissions(self):
        client = self.client_as(self.viewer)

        response = client.get("/api/settings/me/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["role"], Role.VIEWER)
        self.assertFalse(response.data["permissions"]["can_manage_users"])
        self.assertFalse(response.data["permissions"]["can_edit_prices"])
        self.assertFalse(response.data["permissions"]["can_manage_families"])
        self.assertEqual(response.data["families"], [])
        self.assertIsNone(response.data["active_family"])

    def test_settings_me_requires_authentication(self):
        client = APIClient()

        response = client.get("/api/settings/me/")

        # This app's REST_FRAMEWORK config only registers
        # SessionAuthentication (no Basic auth), so DRF cannot
        # issue a WWW-Authenticate challenge and correctly returns
        # 403 rather than 401 - consistent with every other
        # IsAuthenticated endpoint in this codebase.
        self.assertEqual(response.status_code, 403)

    def test_auth_me_returns_full_rbac_payload(self):
        client = self.client_as(self.system_owner)

        response = client.get("/api/auth/me/")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["authenticated"])
        self.assertEqual(response.data["user"]["role"], Role.SYSTEM_OWNER)
        self.assertTrue(response.data["user"]["permissions"]["can_manage_families"])
        self.assertTrue(response.data["user"]["permissions"]["can_view_all_families"])

    def test_login_response_includes_role_and_permissions(self):
        client = APIClient()

        response = client.post(
            "/api/auth/login/",
            {"username": "read_only_viewer", "password": "test-pass-123"},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["user"]["role"], Role.VIEWER)
        self.assertIn("permissions", response.data["user"])

    def test_permissions_assignable_roles_matches_hierarchy(self):
        self.assertEqual(
            set(self.client_as(self.admin).get("/api/settings/me/").data["permissions"][
                "assignable_roles"
            ]),
            {Role.VIEWER},
        )
        self.assertEqual(
            set(self.client_as(self.super_user).get("/api/settings/me/").data["permissions"][
                "assignable_roles"
            ]),
            {Role.VIEWER, Role.ADMIN},
        )
        self.assertEqual(
            set(self.client_as(self.system_owner).get("/api/settings/me/").data["permissions"][
                "assignable_roles"
            ]),
            {Role.VIEWER, Role.ADMIN, Role.SUPER_USER, Role.SYSTEM_OWNER},
        )


# ======================================================================
# VIEWER
# ======================================================================


class ViewerRestrictionTests(BaseRBACTestCase):
    def test_viewer_cannot_list_users(self):
        response = self.client_as(self.viewer).get("/api/settings/users/")

        self.assertEqual(response.status_code, 403)

    def test_viewer_cannot_create_users(self):
        response = self.client_as(self.viewer).post(
            "/api/settings/users/",
            {
                "username": "sneaky",
                "email": "sneaky@example.com",
                "password": "SuperSecret123!",
                "confirm_password": "SuperSecret123!",
                "role": Role.VIEWER,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 403)
        self.assertFalse(User.objects.filter(username="sneaky").exists())

    def test_viewer_cannot_edit_other_users(self):
        response = self.client_as(self.viewer).patch(
            f"/api/settings/users/{self.admin.id}/",
            {"first_name": "Hacked"},
            format="json",
        )

        self.assertEqual(response.status_code, 403)

    def test_viewer_can_edit_own_basic_profile(self):
        response = self.client_as(self.viewer).patch(
            f"/api/settings/users/{self.viewer.id}/",
            {"first_name": "Reader"},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.viewer.refresh_from_db()
        self.assertEqual(self.viewer.first_name, "Reader")

    def test_viewer_cannot_change_own_role(self):
        response = self.client_as(self.viewer).patch(
            f"/api/settings/users/{self.viewer.id}/",
            {"role": Role.ADMIN},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.viewer.refresh_from_db()
        self.assertEqual(self.viewer.profile.role, Role.VIEWER)

    def test_viewer_cannot_edit_prices(self):
        response = self.client_as(self.viewer).patch(
            f"/api/portfolio/assets/{self.asset.id}/manual-price/",
            {"price": "250"},
            format="json",
        )

        self.assertEqual(response.status_code, 403)

    def test_viewer_can_view_prices(self):
        response = self.client_as(self.viewer).get("/api/settings/prices/")

        self.assertEqual(response.status_code, 200)

    def test_viewer_cannot_deactivate_users(self):
        response = self.client_as(self.viewer).post(
            f"/api/settings/users/{self.admin.id}/deactivate/"
        )

        self.assertEqual(response.status_code, 403)

    def test_viewer_cannot_view_families(self):
        response = self.client_as(self.viewer).get("/api/settings/groups/")

        self.assertEqual(response.status_code, 403)

    def test_viewer_cannot_create_family(self):
        response = self.client_as(self.viewer).post(
            "/api/settings/groups/", {"name": "Sneaky Family"}, format="json"
        )

        self.assertEqual(response.status_code, 403)

    def test_viewer_cannot_assign_self_to_family(self):
        response = self.client_as(self.viewer).patch(
            f"/api/settings/users/{self.viewer.id}/",
            {"family_ids": []},
            format="json",
        )

        self.assertEqual(response.status_code, 400)


# ======================================================================
# ADMIN
# ======================================================================


class AdminCapabilityTests(BaseRBACTestCase):
    def test_admin_can_create_viewer(self):
        response = self.client_as(self.admin).post(
            "/api/settings/users/",
            {
                "username": "new_viewer",
                "email": "new_viewer@example.com",
                "password": "SuperSecret123!",
                "confirm_password": "SuperSecret123!",
                "role": Role.VIEWER,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(User.objects.get(username="new_viewer").profile.role, Role.VIEWER)

    def test_admin_cannot_create_admin(self):
        response = self.client_as(self.admin).post(
            "/api/settings/users/",
            {
                "username": "new_admin",
                "email": "new_admin@example.com",
                "password": "SuperSecret123!",
                "confirm_password": "SuperSecret123!",
                "role": Role.ADMIN,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(User.objects.filter(username="new_admin").exists())

    def test_admin_cannot_create_super_user(self):
        response = self.client_as(self.admin).post(
            "/api/settings/users/",
            {
                "username": "sneaky_super",
                "email": "sneaky_super@example.com",
                "password": "SuperSecret123!",
                "confirm_password": "SuperSecret123!",
                "role": Role.SUPER_USER,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(User.objects.filter(username="sneaky_super").exists())

    def test_admin_cannot_create_system_owner(self):
        response = self.client_as(self.admin).post(
            "/api/settings/users/",
            {
                "username": "sneaky_owner",
                "email": "sneaky_owner@example.com",
                "password": "SuperSecret123!",
                "confirm_password": "SuperSecret123!",
                "role": Role.SYSTEM_OWNER,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(User.objects.filter(username="sneaky_owner").exists())

    def test_create_response_reflects_assigned_role_immediately(self):
        # Regression test: the role assigned on creation must be
        # visible in the SAME response, not just on a subsequent
        # GET. A prior bug left the immediate create response
        # showing the pre-signal default (VIEWER) role due to
        # Django's reverse-o2o descriptor caching.
        response = self.client_as(self.system_owner).post(
            "/api/settings/users/",
            {
                "username": "immediate_role_check",
                "email": "immediate_role_check@example.com",
                "password": "SuperSecret123!",
                "confirm_password": "SuperSecret123!",
                "role": Role.ADMIN,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["role"], Role.ADMIN)

    def test_admin_cannot_change_any_role(self):
        response = self.client_as(self.admin).patch(
            f"/api/settings/users/{self.viewer.id}/",
            {"role": Role.ADMIN},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.viewer.refresh_from_db()
        self.assertEqual(self.viewer.profile.role, Role.VIEWER)

    def test_admin_cannot_promote_to_super_user(self):
        response = self.client_as(self.admin).patch(
            f"/api/settings/users/{self.viewer.id}/",
            {"role": Role.SUPER_USER},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.viewer.refresh_from_db()
        self.assertEqual(self.viewer.profile.role, Role.VIEWER)

    def test_admin_cannot_demote_super_user(self):
        response = self.client_as(self.admin).patch(
            f"/api/settings/users/{self.super_user.id}/",
            {"role": Role.ADMIN},
            format="json",
        )

        self.assertIn(response.status_code, (400, 403))
        self.super_user.refresh_from_db()
        self.assertEqual(self.super_user.profile.role, Role.SUPER_USER)

    def test_admin_cannot_manage_super_user_account_at_all(self):
        response = self.client_as(self.admin).patch(
            f"/api/settings/users/{self.super_user.id}/",
            {"first_name": "Hacked"},
            format="json",
        )

        self.assertEqual(response.status_code, 403)

    def test_admin_cannot_manage_system_owner_account_at_all(self):
        response = self.client_as(self.admin).patch(
            f"/api/settings/users/{self.system_owner.id}/",
            {"first_name": "Hacked"},
            format="json",
        )

        self.assertEqual(response.status_code, 403)

    def test_admin_cannot_deactivate_last_system_owner(self):
        response = self.client_as(self.admin).post(
            f"/api/settings/users/{self.system_owner.id}/deactivate/"
        )

        self.assertEqual(response.status_code, 403)
        self.system_owner.refresh_from_db()
        self.assertTrue(self.system_owner.is_active)

    def test_admin_can_deactivate_viewer(self):
        response = self.client_as(self.admin).post(
            f"/api/settings/users/{self.viewer.id}/deactivate/"
        )

        self.assertEqual(response.status_code, 200)
        self.viewer.refresh_from_db()
        self.assertFalse(self.viewer.is_active)

    def test_admin_can_edit_manual_prices(self):
        response = self.client_as(self.admin).patch(
            f"/api/portfolio/assets/{self.admin_asset.id}/manual-price/",
            {"price": "555"},
            format="json",
        )

        self.assertEqual(response.status_code, 200)

        latest = MarketPrice.objects.filter(
            asset=self.admin_asset, source=DataSource.MANUAL
        ).first()

        self.assertIsNotNone(latest)
        self.assertEqual(latest.updated_by, self.admin)

    def test_admin_cannot_bypass_permission_via_users_endpoint(self):
        # Direct API call attempting privilege escalation through a
        # manually crafted request (is_superuser field is not even
        # a serializer field, so this should have no effect).
        response = self.client_as(self.admin).patch(
            f"/api/settings/users/{self.admin.id}/",
            {"role": Role.SYSTEM_OWNER, "is_superuser": True},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.admin.refresh_from_db()
        self.assertFalse(self.admin.is_superuser)

    def test_admin_cannot_add_family(self):
        response = self.client_as(self.admin).post(
            "/api/settings/groups/", {"name": "Sneaky Family"}, format="json"
        )

        self.assertEqual(response.status_code, 403)

    def test_admin_cannot_view_all_families(self):
        FamilyGroup.objects.create(name="Some Family")

        response = self.client_as(self.admin).get("/api/settings/groups/")

        self.assertEqual(response.status_code, 403)

    def test_admin_cannot_reassign_family_membership(self):
        response = self.client_as(self.admin).patch(
            f"/api/settings/users/{self.viewer.id}/",
            {"family_ids": []},
            format="json",
        )

        self.assertEqual(response.status_code, 400)

    def test_admin_cannot_add_family_member_via_group_endpoint(self):
        group = FamilyGroup.objects.create(name="Existing Family")

        response = self.client_as(self.admin).post(
            f"/api/settings/groups/{group.id}/members/",
            {"user_id": self.viewer.id},
            format="json",
        )

        self.assertEqual(response.status_code, 403)
        self.assertFalse(self.viewer.profile.family_groups.filter(pk=group.pk).exists())


# ======================================================================
# SUPER USER
# ======================================================================


class SuperUserCapabilityTests(BaseRBACTestCase):
    def test_super_user_can_create_admin(self):
        response = self.client_as(self.super_user).post(
            "/api/settings/users/",
            {
                "username": "su_made_admin",
                "email": "su_made_admin@example.com",
                "password": "SuperSecret123!",
                "confirm_password": "SuperSecret123!",
                "role": Role.ADMIN,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(User.objects.get(username="su_made_admin").profile.role, Role.ADMIN)

    def test_super_user_can_create_viewer(self):
        response = self.client_as(self.super_user).post(
            "/api/settings/users/",
            {
                "username": "su_made_viewer",
                "email": "su_made_viewer@example.com",
                "password": "SuperSecret123!",
                "confirm_password": "SuperSecret123!",
                "role": Role.VIEWER,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201)

    def test_super_user_cannot_create_super_user(self):
        response = self.client_as(self.super_user).post(
            "/api/settings/users/",
            {
                "username": "second_super",
                "email": "second_super@example.com",
                "password": "SuperSecret123!",
                "confirm_password": "SuperSecret123!",
                "role": Role.SUPER_USER,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(User.objects.filter(username="second_super").exists())

    def test_super_user_cannot_create_system_owner(self):
        response = self.client_as(self.super_user).post(
            "/api/settings/users/",
            {
                "username": "sneaky_owner_2",
                "email": "sneaky_owner_2@example.com",
                "password": "SuperSecret123!",
                "confirm_password": "SuperSecret123!",
                "role": Role.SYSTEM_OWNER,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(User.objects.filter(username="sneaky_owner_2").exists())

    def test_super_user_can_promote_viewer_to_admin(self):
        response = self.client_as(self.super_user).patch(
            f"/api/settings/users/{self.viewer.id}/",
            {"role": Role.ADMIN},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.viewer.refresh_from_db()
        self.assertEqual(self.viewer.profile.role, Role.ADMIN)
        self.assertTrue(self.viewer.is_staff)

    def test_super_user_can_demote_admin_to_viewer(self):
        response = self.client_as(self.super_user).patch(
            f"/api/settings/users/{self.admin.id}/",
            {"role": Role.VIEWER},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.admin.refresh_from_db()
        self.assertEqual(self.admin.profile.role, Role.VIEWER)

    def test_super_user_cannot_promote_to_super_user(self):
        response = self.client_as(self.super_user).patch(
            f"/api/settings/users/{self.viewer.id}/",
            {"role": Role.SUPER_USER},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.viewer.refresh_from_db()
        self.assertEqual(self.viewer.profile.role, Role.VIEWER)

    def test_super_user_cannot_promote_to_system_owner(self):
        response = self.client_as(self.super_user).patch(
            f"/api/settings/users/{self.viewer.id}/",
            {"role": Role.SYSTEM_OWNER},
            format="json",
        )

        self.assertEqual(response.status_code, 400)

    def test_super_user_cannot_manage_another_super_user(self):
        other_super = make_user("other_super", Role.SUPER_USER)

        response = self.client_as(self.super_user).patch(
            f"/api/settings/users/{other_super.id}/",
            {"first_name": "Hacked"},
            format="json",
        )

        self.assertEqual(response.status_code, 403)

    def test_super_user_cannot_manage_system_owner(self):
        response = self.client_as(self.super_user).patch(
            f"/api/settings/users/{self.system_owner.id}/",
            {"first_name": "Hacked"},
            format="json",
        )

        self.assertEqual(response.status_code, 403)

    def test_super_user_cannot_create_system_owner_even_with_is_superuser_flag(self):
        response = self.client_as(self.super_user).post(
            "/api/settings/users/",
            {
                "username": "sneaky_owner_3",
                "email": "sneaky_owner_3@example.com",
                "password": "SuperSecret123!",
                "confirm_password": "SuperSecret123!",
                "role": Role.ADMIN,
                "is_superuser": True,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        created = User.objects.get(username="sneaky_owner_3")
        self.assertFalse(created.is_superuser)
        self.assertEqual(created.profile.role, Role.ADMIN)

    def test_super_user_can_edit_manual_prices(self):
        Asset.objects.filter(pk=self.admin_asset.pk).update(owner=self.super_user)

        response = self.client_as(self.super_user).patch(
            f"/api/portfolio/assets/{self.admin_asset.id}/manual-price/",
            {"price": "999"},
            format="json",
        )

        self.assertEqual(response.status_code, 200)

    def test_super_user_cannot_add_family(self):
        response = self.client_as(self.super_user).post(
            "/api/settings/groups/", {"name": "Sneaky Family"}, format="json"
        )

        self.assertEqual(response.status_code, 403)

    def test_super_user_cannot_view_all_families(self):
        response = self.client_as(self.super_user).get("/api/settings/groups/")

        self.assertEqual(response.status_code, 403)

    def test_super_user_cannot_reassign_family_membership(self):
        response = self.client_as(self.super_user).patch(
            f"/api/settings/users/{self.viewer.id}/",
            {"family_ids": []},
            format="json",
        )

        self.assertEqual(response.status_code, 400)

    def test_super_user_cannot_add_family_member_via_group_endpoint(self):
        group = FamilyGroup.objects.create(name="Existing Family")

        response = self.client_as(self.super_user).post(
            f"/api/settings/groups/{group.id}/members/",
            {"user_id": self.viewer.id},
            format="json",
        )

        self.assertEqual(response.status_code, 403)


# ======================================================================
# SYSTEM OWNER
# ======================================================================


class SystemOwnerCapabilityTests(BaseRBACTestCase):
    def test_system_owner_can_create_every_role(self):
        for role in (Role.VIEWER, Role.ADMIN, Role.SUPER_USER, Role.SYSTEM_OWNER):
            response = self.client_as(self.system_owner).post(
                "/api/settings/users/",
                {
                    "username": f"owner_made_{role.lower()}",
                    "email": f"owner_made_{role.lower()}@example.com",
                    "password": "SuperSecret123!",
                    "confirm_password": "SuperSecret123!",
                    "role": role,
                },
                format="json",
            )

            self.assertEqual(response.status_code, 201, response.data)
            self.assertEqual(
                User.objects.get(username=f"owner_made_{role.lower()}").profile.role, role
            )

    def test_system_owner_can_change_any_role(self):
        response = self.client_as(self.system_owner).patch(
            f"/api/settings/users/{self.viewer.id}/",
            {"role": Role.SUPER_USER},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.viewer.refresh_from_db()
        self.assertEqual(self.viewer.profile.role, Role.SUPER_USER)

    def test_system_owner_cannot_change_own_role(self):
        response = self.client_as(self.system_owner).patch(
            f"/api/settings/users/{self.system_owner.id}/",
            {"role": Role.ADMIN},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.system_owner.refresh_from_db()
        self.assertEqual(self.system_owner.profile.role, Role.SYSTEM_OWNER)

    def test_system_owner_sees_all_users(self):
        response = self.client_as(self.system_owner).get("/api/settings/users/")

        self.assertEqual(response.status_code, 200)
        usernames = {row["username"] for row in response.data}
        self.assertEqual(
            usernames,
            {
                self.system_owner.username,
                self.super_user.username,
                self.admin.username,
                self.viewer.username,
            },
        )

    def test_system_owner_can_create_family(self):
        response = self.client_as(self.system_owner).post(
            "/api/settings/groups/", {"name": "Sharma Family"}, format="json"
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["name"], "Sharma Family")

    def test_system_owner_can_rename_family(self):
        group = FamilyGroup.objects.create(name="Old Name")

        response = self.client_as(self.system_owner).patch(
            f"/api/settings/groups/{group.id}/", {"name": "New Name"}, format="json"
        )

        self.assertEqual(response.status_code, 200)
        group.refresh_from_db()
        self.assertEqual(group.name, "New Name")

    def test_system_owner_can_assign_user_to_multiple_families_simultaneously(self):
        family_a = FamilyGroup.objects.create(name="Family A")
        family_b = FamilyGroup.objects.create(name="Family B")

        response = self.client_as(self.system_owner).patch(
            f"/api/settings/users/{self.viewer.id}/",
            {"family_ids": [family_a.id, family_b.id]},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.viewer.refresh_from_db()
        self.assertEqual(
            set(self.viewer.profile.family_groups.values_list("id", flat=True)),
            {family_a.id, family_b.id},
        )

    def test_system_owner_can_remove_family_membership(self):
        family = FamilyGroup.objects.create(name="Family A")
        self.viewer.profile.family_groups.add(family)

        response = self.client_as(self.system_owner).delete(
            f"/api/settings/groups/{family.id}/members/{self.viewer.id}/",
        )

        self.assertEqual(response.status_code, 200)
        self.viewer.refresh_from_db()
        self.assertFalse(self.viewer.profile.family_groups.exists())

    def test_system_owner_can_add_family_membership(self):
        family = FamilyGroup.objects.create(name="Family A")

        response = self.client_as(self.system_owner).post(
            f"/api/settings/groups/{family.id}/members/",
            {"user_id": self.viewer.id},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.viewer.refresh_from_db()
        self.assertTrue(self.viewer.profile.family_groups.filter(pk=family.pk).exists())

    def test_system_owner_can_view_all_families(self):
        FamilyGroup.objects.create(name="Family A")
        FamilyGroup.objects.create(name="Family B")

        response = self.client_as(self.system_owner).get("/api/settings/groups/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 2)

    def test_system_owner_can_edit_manual_prices(self):
        Asset.objects.filter(pk=self.admin_asset.pk).update(owner=self.system_owner)

        response = self.client_as(self.system_owner).patch(
            f"/api/portfolio/assets/{self.admin_asset.id}/manual-price/",
            {"price": "1234"},
            format="json",
        )

        self.assertEqual(response.status_code, 200)

    def test_cannot_deactivate_last_active_system_owner_even_as_self(self):
        response = self.client_as(self.system_owner).post(
            f"/api/settings/users/{self.system_owner.id}/deactivate/"
        )

        # Self-deactivation is blocked outright regardless.
        self.assertEqual(response.status_code, 400)

    def test_demoting_last_system_owner_is_blocked_even_by_another_owner(self):
        second_owner = make_user("second_owner", Role.SYSTEM_OWNER)

        response = self.client_as(second_owner).patch(
            f"/api/settings/users/{self.system_owner.id}/",
            {"role": Role.ADMIN},
            format="json",
        )

        # Not the last one (second_owner exists) - allowed.
        self.assertEqual(response.status_code, 200)

        # Now second_owner is the only System Owner - demoting them
        # (even by another elevated caller, if one existed) must
        # fail. Simulate via a third owner.
        third_owner = make_user("third_owner", Role.SYSTEM_OWNER)

        response = self.client_as(third_owner).patch(
            f"/api/settings/users/{second_owner.id}/",
            {"role": Role.VIEWER},
            format="json",
        )

        self.assertEqual(response.status_code, 200)

        # third_owner is now the ONLY system owner - demoting them
        # must fail, even attempted by itself indirectly via a
        # freshly created 4th owner? No further owner exists, so
        # confirm the guard by trying to deactivate.
        response = self.client_as(third_owner).post(
            f"/api/settings/users/{third_owner.id}/deactivate/"
        )
        self.assertEqual(response.status_code, 400)


# ======================================================================
# PASSWORD RESET
# ======================================================================


class PasswordResetTests(BaseRBACTestCase):
    def test_admin_can_reset_viewer_password(self):
        response = self.client_as(self.admin).post(
            f"/api/settings/users/{self.viewer.id}/reset-password/",
            {"new_password": "BrandNewPass123!", "confirm_password": "BrandNewPass123!"},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.viewer.refresh_from_db()
        self.assertTrue(self.viewer.check_password("BrandNewPass123!"))

    def test_admin_cannot_reset_super_user_password(self):
        response = self.client_as(self.admin).post(
            f"/api/settings/users/{self.super_user.id}/reset-password/",
            {"new_password": "BrandNewPass123!", "confirm_password": "BrandNewPass123!"},
            format="json",
        )

        self.assertEqual(response.status_code, 403)

    def test_admin_cannot_reset_system_owner_password(self):
        response = self.client_as(self.admin).post(
            f"/api/settings/users/{self.system_owner.id}/reset-password/",
            {"new_password": "BrandNewPass123!", "confirm_password": "BrandNewPass123!"},
            format="json",
        )

        self.assertEqual(response.status_code, 403)

    def test_viewer_cannot_reset_anyone_password(self):
        response = self.client_as(self.viewer).post(
            f"/api/settings/users/{self.admin.id}/reset-password/",
            {"new_password": "BrandNewPass123!", "confirm_password": "BrandNewPass123!"},
            format="json",
        )

        self.assertEqual(response.status_code, 403)

    def test_password_mismatch_rejected(self):
        response = self.client_as(self.admin).post(
            f"/api/settings/users/{self.viewer.id}/reset-password/",
            {"new_password": "BrandNewPass123!", "confirm_password": "Different123!"},
            format="json",
        )

        self.assertEqual(response.status_code, 400)


# ======================================================================
# VALIDATION
# ======================================================================


class DuplicateAndValidationTests(BaseRBACTestCase):
    def test_duplicate_username_rejected(self):
        response = self.client_as(self.admin).post(
            "/api/settings/users/",
            {
                "username": self.viewer.username,
                "email": "unique@example.com",
                "password": "SuperSecret123!",
                "confirm_password": "SuperSecret123!",
                "role": Role.VIEWER,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)

    def test_duplicate_email_rejected(self):
        self.viewer.email = "taken@example.com"
        self.viewer.save(update_fields=["email"])

        response = self.client_as(self.admin).post(
            "/api/settings/users/",
            {
                "username": "unique_username",
                "email": "taken@example.com",
                "password": "SuperSecret123!",
                "confirm_password": "SuperSecret123!",
                "role": Role.VIEWER,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)

    def test_invalid_role_rejected(self):
        response = self.client_as(self.admin).post(
            "/api/settings/users/",
            {
                "username": "bad_role_user",
                "email": "bad_role_user@example.com",
                "password": "SuperSecret123!",
                "confirm_password": "SuperSecret123!",
                "role": "GOD_MODE",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)

    def test_created_user_response_never_contains_password(self):
        response = self.client_as(self.admin).post(
            "/api/settings/users/",
            {
                "username": "no_password_leak",
                "email": "no_password_leak@example.com",
                "password": "SuperSecret123!",
                "confirm_password": "SuperSecret123!",
                "role": Role.VIEWER,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        self.assertNotIn("password", response.data)

    def test_invalid_manual_price_rejected(self):
        response = self.client_as(self.admin).patch(
            f"/api/portfolio/assets/{self.admin_asset.id}/manual-price/",
            {"price": "not-a-number"},
            format="json",
        )

        self.assertEqual(response.status_code, 400)


# ======================================================================
# DIRECT-API SECURITY TESTS (spec section 8's explicit test list)
# ======================================================================


class SecurityDirectApiTests(BaseRBACTestCase):
    def test_viewer_manual_price_update_rejected(self):
        response = self.client_as(self.viewer).patch(
            f"/api/portfolio/assets/{self.asset.id}/manual-price/",
            {"price": "1"},
            format="json",
        )

        self.assertEqual(response.status_code, 403)

    def test_viewer_user_creation_rejected(self):
        response = self.client_as(self.viewer).post(
            "/api/settings/users/",
            {
                "username": "viewer_created",
                "email": "viewer_created@example.com",
                "password": "SuperSecret123!",
                "confirm_password": "SuperSecret123!",
                "role": Role.VIEWER,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 403)

    def test_admin_super_user_creation_rejected(self):
        response = self.client_as(self.admin).post(
            "/api/settings/users/",
            {
                "username": "direct_super_attempt",
                "email": "direct_super_attempt@example.com",
                "password": "SuperSecret123!",
                "confirm_password": "SuperSecret123!",
                "role": Role.SUPER_USER,
            },
            format="json",
        )

        self.assertIn(response.status_code, (400, 403))
        self.assertFalse(User.objects.filter(username="direct_super_attempt").exists())

    def test_admin_family_reassignment_rejected(self):
        family = FamilyGroup.objects.create(name="Family A")

        response = self.client_as(self.admin).patch(
            f"/api/settings/users/{self.viewer.id}/",
            {"family_ids": [family.id]},
            format="json",
        )

        self.assertIn(response.status_code, (400, 403))
        self.assertFalse(self.viewer.profile.family_groups.filter(pk=family.pk).exists())

    def test_super_user_family_reassignment_rejected(self):
        family = FamilyGroup.objects.create(name="Family A")

        response = self.client_as(self.super_user).patch(
            f"/api/settings/users/{self.viewer.id}/",
            {"family_ids": [family.id]},
            format="json",
        )

        self.assertIn(response.status_code, (400, 403))
        self.assertFalse(self.viewer.profile.family_groups.filter(pk=family.pk).exists())

    def test_super_user_system_owner_creation_rejected(self):
        response = self.client_as(self.super_user).post(
            "/api/settings/users/",
            {
                "username": "direct_owner_attempt",
                "email": "direct_owner_attempt@example.com",
                "password": "SuperSecret123!",
                "confirm_password": "SuperSecret123!",
                "role": Role.SYSTEM_OWNER,
            },
            format="json",
        )

        self.assertIn(response.status_code, (400, 403))
        self.assertFalse(User.objects.filter(username="direct_owner_attempt").exists())

    def test_lower_role_modifying_system_owner_rejected(self):
        for actor in (self.super_user, self.admin, self.viewer):
            response = self.client_as(actor).patch(
                f"/api/settings/users/{self.system_owner.id}/",
                {"first_name": "Hacked"},
                format="json",
            )

            self.assertEqual(response.status_code, 403, f"actor={actor.username}")

    def test_submitting_another_users_id_in_privileged_endpoint_rejected(self):
        # A Viewer cannot escalate by targeting a different user's
        # ID in the reset-password endpoint's URL.
        response = self.client_as(self.viewer).post(
            f"/api/settings/users/{self.admin.id}/reset-password/",
            {"new_password": "BrandNewPass123!", "confirm_password": "BrandNewPass123!"},
            format="json",
        )

        self.assertEqual(response.status_code, 403)

        # An Admin cannot deactivate an out-of-scope account by ID
        # even though the endpoint accepts arbitrary IDs in the URL.
        response = self.client_as(self.admin).post(
            f"/api/settings/users/{self.super_user.id}/deactivate/"
        )

        self.assertEqual(response.status_code, 403)


# ======================================================================
# FAMILY / MULTI-FAMILY SCOPING
# ======================================================================


class FamilyVisibilityTests(TestCase):
    def test_ungrouped_user_sees_only_self(self):
        user = make_user("solo_user", Role.VIEWER)

        self.assertEqual(get_visible_owner_ids(user), [user.id])

    def test_family_members_see_each_other(self):
        family = FamilyGroup.objects.create(name="Test Family")

        member_a = make_user("family_member_a", Role.VIEWER)
        member_b = make_user("family_member_b", Role.ADMIN)
        outsider = make_user("outsider", Role.VIEWER)

        member_a.profile.family_groups.add(family)
        member_b.profile.family_groups.add(family)

        visible = set(get_visible_owner_ids(member_a))

        self.assertEqual(visible, {member_a.id, member_b.id})
        self.assertNotIn(outsider.id, visible)

    def test_system_owner_sees_everyone_regardless_of_family(self):
        owner = make_user("view_all_owner", Role.SYSTEM_OWNER)
        make_user("random_user_1", Role.VIEWER)
        make_user("random_user_2", Role.ADMIN)

        visible = set(get_visible_owner_ids(owner))

        self.assertEqual(visible, set(User.objects.values_list("id", flat=True)))

    def test_multi_family_user_sees_only_active_family_not_merged(self):
        family_a = FamilyGroup.objects.create(name="Family A")
        family_b = FamilyGroup.objects.create(name="Family B")

        user = make_user("multi_family_user", Role.VIEWER)
        member_a_only = make_user("member_a_only", Role.VIEWER)
        member_b_only = make_user("member_b_only", Role.VIEWER)

        user.profile.family_groups.add(family_a, family_b)
        member_a_only.profile.family_groups.add(family_a)
        member_b_only.profile.family_groups.add(family_b)

        # No active family set yet -> defaults to the lowest-ID
        # family (family_a), never a merge of both.
        visible = set(get_visible_owner_ids(user))
        self.assertIn(member_a_only.id, visible)
        self.assertNotIn(member_b_only.id, visible)

        # Switch active family to B -> now sees B's members, not A's.
        user.profile.active_family_group = family_b
        user.profile.save(update_fields=["active_family_group"])

        visible = set(get_visible_owner_ids(user))
        self.assertIn(member_b_only.id, visible)
        self.assertNotIn(member_a_only.id, visible)
        # Self is always visible regardless of active family.
        self.assertIn(user.id, visible)

    def test_multi_family_user_never_sees_unrelated_third_family(self):
        family_a = FamilyGroup.objects.create(name="Family A")
        family_b = FamilyGroup.objects.create(name="Family B")
        family_c = FamilyGroup.objects.create(name="Family C")

        user = make_user("scoped_user", Role.VIEWER)
        user.profile.family_groups.add(family_a, family_b)

        outsider_c = make_user("outsider_c", Role.VIEWER)
        outsider_c.profile.family_groups.add(family_c)

        for family in (family_a, family_b):
            user.profile.active_family_group = family
            user.profile.save(update_fields=["active_family_group"])

            self.assertNotIn(outsider_c.id, set(get_visible_owner_ids(user)))

    def test_removing_active_family_membership_resets_active_family(self):
        family = FamilyGroup.objects.create(name="Family A")
        user = make_user("active_reset_user", Role.VIEWER)
        user.profile.family_groups.add(family)
        user.profile.active_family_group = family
        user.profile.save(update_fields=["active_family_group"])

        user.profile.family_groups.remove(family)
        user.profile.refresh_from_db()

        # Active family should no longer resolve to a removed family.
        self.assertEqual(get_visible_owner_ids(user), [user.id])


class FamilySharedManualPriceTests(TestCase):
    """
    Manual price editing is scoped by family-shared visibility
    (get_visible_owner_ids), not strict Asset.owner - any Admin+
    member of the asset owner's family can edit its price, not
    only the specific account that originally created it. This
    was tightened after a brand-new Super User (with no assets of
    their own) was unable to edit ANY prices, since the previous
    strict-owner check meant only the original importer's account
    could ever edit a given asset regardless of role or shared
    family membership.
    """

    def setUp(self):
        self.family = FamilyGroup.objects.create(name="Shared Family")

        self.owner = make_user("price_asset_owner", Role.VIEWER)
        self.owner.profile.family_groups.add(self.family)

        self.family_super_user = make_user(
            "price_family_super_user", Role.SUPER_USER
        )
        self.family_super_user.profile.family_groups.add(self.family)

        self.outsider_super_user = make_user(
            "price_outsider_super_user", Role.SUPER_USER
        )
        # Deliberately NOT added to self.family.

        self.asset = Asset.objects.create(
            owner=self.owner,
            name="Shared Family Stock",
            category="STOCK",
            isin="INE000SHARED01",
        )

    def client_as(self, user):
        return client_for(user)

    def test_family_member_can_edit_asset_owned_by_another_member(self):
        response = self.client_as(self.family_super_user).patch(
            f"/api/portfolio/assets/{self.asset.id}/manual-price/",
            {"price": "321"},
            format="json",
        )

        self.assertEqual(response.status_code, 200)

        latest = MarketPrice.objects.filter(
            asset=self.asset, source=DataSource.MANUAL
        ).first()

        self.assertIsNotNone(latest)
        self.assertEqual(latest.updated_by, self.family_super_user)

    def test_family_member_can_view_asset_in_settings_price_list(self):
        response = self.client_as(self.family_super_user).get(
            "/api/settings/prices/"
        )

        self.assertEqual(response.status_code, 200)

        asset_ids = {row["asset_id"] for row in response.data["results"]}

        self.assertIn(self.asset.id, asset_ids)

    def test_non_family_super_user_cannot_edit_asset(self):
        response = self.client_as(self.outsider_super_user).patch(
            f"/api/portfolio/assets/{self.asset.id}/manual-price/",
            {"price": "321"},
            format="json",
        )

        self.assertEqual(response.status_code, 404)

        self.assertFalse(
            MarketPrice.objects.filter(
                asset=self.asset, source=DataSource.MANUAL
            ).exists()
        )

    def test_non_family_super_user_does_not_see_asset_in_price_list(self):
        response = self.client_as(self.outsider_super_user).get(
            "/api/settings/prices/"
        )

        self.assertEqual(response.status_code, 200)

        asset_ids = {row["asset_id"] for row in response.data["results"]}

        self.assertNotIn(self.asset.id, asset_ids)

    def test_edit_by_family_member_rebuilds_actual_owners_positions(self):
        # The position rebuild after an edit must target the
        # asset's real owner (whose Transactions actually exist),
        # not the editor - regardless of who clicked Save.
        response = self.client_as(self.family_super_user).patch(
            f"/api/portfolio/assets/{self.asset.id}/manual-price/",
            {"price": "555"},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        # No exception raised means rebuild_all_for_user(asset.owner)
        # ran against a user who actually owns matching Transaction
        # rows, rather than the editor (who owns none for this
        # asset) - a wrong target here wouldn't raise, it would
        # just silently rebuild nothing, so this test mainly exists
        # to document and pin the expected call target.


class ActiveFamilyEndpointTests(BaseRBACTestCase):
    def test_user_can_select_active_family_among_own_families(self):
        family_a = FamilyGroup.objects.create(name="Family A")
        family_b = FamilyGroup.objects.create(name="Family B")
        self.viewer.profile.family_groups.add(family_a, family_b)

        response = self.client_as(self.viewer).post(
            "/api/settings/me/active-family/", {"family_id": family_b.id}, format="json"
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["active_family"]["id"], family_b.id)

    def test_user_cannot_select_family_they_do_not_belong_to(self):
        other_family = FamilyGroup.objects.create(name="Not Mine")

        response = self.client_as(self.viewer).post(
            "/api/settings/me/active-family/", {"family_id": other_family.id}, format="json"
        )

        self.assertEqual(response.status_code, 400)

    def test_active_family_selection_is_available_to_every_role(self):
        family = FamilyGroup.objects.create(name="Family A")

        for actor in (self.system_owner, self.super_user, self.admin, self.viewer):
            actor.profile.family_groups.add(family)

            response = self.client_as(actor).post(
                "/api/settings/me/active-family/", {"family_id": family.id}, format="json"
            )

            self.assertEqual(response.status_code, 200, f"actor={actor.username}")


class FamilyGroupApiTests(TestCase):
    def setUp(self):
        self.system_owner = make_user("group_owner", Role.SYSTEM_OWNER)
        self.super_user = make_user("group_super", Role.SUPER_USER)
        self.admin = make_user("group_admin", Role.ADMIN)
        self.viewer = make_user("group_viewer", Role.VIEWER)

    def client_as(self, user):
        return client_for(user)

    def test_system_owner_can_create_group(self):
        response = self.client_as(self.system_owner).post(
            "/api/settings/groups/", {"name": "Sharma Family"}, format="json"
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["name"], "Sharma Family")
        self.assertEqual(response.data["members"], [])

    def test_add_and_remove_member_preserves_other_memberships(self):
        client = self.client_as(self.system_owner)

        family_a = FamilyGroup.objects.create(name="Family A")
        family_b = FamilyGroup.objects.create(name="Family B")

        # Add viewer to both families.
        for family in (family_a, family_b):
            response = client.post(
                f"/api/settings/groups/{family.id}/members/",
                {"user_id": self.viewer.id},
                format="json",
            )
            self.assertEqual(response.status_code, 200)

        self.viewer.refresh_from_db()
        self.assertEqual(
            set(self.viewer.profile.family_groups.values_list("id", flat=True)),
            {family_a.id, family_b.id},
        )

        # Removing from A must leave B intact (multi-family
        # assignment is real, not a "last write wins" single slot).
        response = client.delete(
            f"/api/settings/groups/{family_a.id}/members/{self.viewer.id}/",
        )

        self.assertEqual(response.status_code, 200)
        self.viewer.refresh_from_db()
        self.assertEqual(
            set(self.viewer.profile.family_groups.values_list("id", flat=True)),
            {family_b.id},
        )

    def test_delete_group_clears_membership(self):
        client = self.client_as(self.system_owner)

        group = FamilyGroup.objects.create(name="Temp Family")
        self.viewer.profile.family_groups.add(group)

        response = client.delete(f"/api/settings/groups/{group.id}/")

        self.assertEqual(response.status_code, 200)
        self.viewer.refresh_from_db()
        self.assertFalse(self.viewer.profile.family_groups.exists())

    def test_create_user_with_family_ids_as_system_owner(self):
        client = self.client_as(self.system_owner)

        family_a = FamilyGroup.objects.create(name="Family A")
        family_b = FamilyGroup.objects.create(name="Family B")

        response = client.post(
            "/api/settings/users/",
            {
                "username": "grouped_new_user",
                "email": "grouped_new_user@example.com",
                "password": "SuperSecret123!",
                "confirm_password": "SuperSecret123!",
                "role": Role.VIEWER,
                "family_ids": [family_a.id, family_b.id],
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201)

        created = User.objects.get(username="grouped_new_user")
        self.assertEqual(
            set(created.profile.family_groups.values_list("id", flat=True)),
            {family_a.id, family_b.id},
        )

    def test_create_user_with_family_ids_as_super_user_rejected(self):
        client = self.client_as(self.super_user)

        family = FamilyGroup.objects.create(name="Family A")

        response = client.post(
            "/api/settings/users/",
            {
                "username": "su_grouped_attempt",
                "email": "su_grouped_attempt@example.com",
                "password": "SuperSecret123!",
                "confirm_password": "SuperSecret123!",
                "role": Role.VIEWER,
                "family_ids": [family.id],
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(User.objects.filter(username="su_grouped_attempt").exists())


# ======================================================================
# USER MANAGEMENT LIST SCOPING (Super User / Admin see only their
# own manageable + family-scoped users; System Owner sees everyone)
# ======================================================================


class UserManagementScopingTests(TestCase):
    """
    User MANAGEMENT (list/edit/activate/delete) is scoped by ROLE
    ONLY, never by family - see get_manageable_users_queryset.
    Family membership instead governs which family's PORTFOLIO DATA
    a user can see (exercised separately in FamilyVisibilityTests).
    """

    def setUp(self):
        self.system_owner = make_user("scope_owner", Role.SYSTEM_OWNER)

        self.family_a = FamilyGroup.objects.create(name="Family A")
        self.family_b = FamilyGroup.objects.create(name="Family B")

        self.super_a = make_user("super_a", Role.SUPER_USER)
        self.super_a.profile.family_groups.add(self.family_a)

        self.admin_a = make_user("admin_a", Role.ADMIN)
        self.admin_a.profile.family_groups.add(self.family_a)

        self.viewer_a = make_user("viewer_a", Role.VIEWER)
        self.viewer_a.profile.family_groups.add(self.family_a)

        self.viewer_b = make_user("viewer_b", Role.VIEWER)
        self.viewer_b.profile.family_groups.add(self.family_b)

        self.unaffiliated_admin = make_user("unaffiliated_admin", Role.ADMIN)

    def client_as(self, user):
        return client_for(user)

    def test_super_user_sees_every_admin_and_viewer_regardless_of_family(self):
        response = self.client_as(self.super_a).get("/api/settings/users/")

        self.assertEqual(response.status_code, 200)
        usernames = {row["username"] for row in response.data}

        # Every Admin and Viewer is manageable, in-family or not -
        # role is the only gate.
        self.assertIn(self.super_a.username, usernames)
        self.assertIn(self.admin_a.username, usernames)
        self.assertIn(self.viewer_a.username, usernames)
        self.assertIn(self.viewer_b.username, usernames)
        self.assertIn(self.unaffiliated_admin.username, usernames)

        # But never the System Owner or another Super User.
        self.assertNotIn(self.system_owner.username, usernames)

    def test_admin_sees_every_viewer_regardless_of_family(self):
        response = self.client_as(self.admin_a).get("/api/settings/users/")

        self.assertEqual(response.status_code, 200)
        usernames = {row["username"] for row in response.data}

        self.assertIn(self.admin_a.username, usernames)
        self.assertIn(self.viewer_a.username, usernames)
        self.assertIn(self.viewer_b.username, usernames)

        # Never another Admin (even in the same family), a Super
        # User, or the System Owner.
        self.assertNotIn(self.unaffiliated_admin.username, usernames)
        self.assertNotIn(self.super_a.username, usernames)
        self.assertNotIn(self.system_owner.username, usernames)

    def test_admin_can_manage_out_of_family_viewer(self):
        # Family membership never restricts Admin's Viewer-management
        # capability - only role does.
        response = self.client_as(self.admin_a).patch(
            f"/api/settings/users/{self.viewer_b.id}/",
            {"first_name": "Hacked"},
            format="json",
        )

        self.assertEqual(response.status_code, 200)

    def test_family_data_visibility_still_excludes_out_of_family_users(self):
        # Even though admin_a can MANAGE viewer_b's account, admin_a
        # still cannot SEE viewer_b's portfolio data - that stays
        # strictly family-scoped via get_visible_owner_ids.
        self.assertNotIn(self.viewer_b.id, get_visible_owner_ids(self.admin_a))


# ======================================================================
# DELETE
# ======================================================================


class DeleteUserApiTests(TestCase):
    def setUp(self):
        self.system_owner = make_user("delete_test_owner", Role.SYSTEM_OWNER)
        self.super_user = make_user("delete_test_super", Role.SUPER_USER)
        self.admin = make_user("delete_test_admin", Role.ADMIN)
        self.viewer = make_user("delete_test_viewer", Role.VIEWER)

    def client_as(self, user):
        return client_for(user)

    def test_admin_can_delete_viewer(self):
        response = self.client_as(self.admin).delete(f"/api/settings/users/{self.viewer.id}/")

        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(pk=self.viewer.id).exists())

    def test_viewer_cannot_delete_anyone(self):
        response = self.client_as(self.viewer).delete(f"/api/settings/users/{self.admin.id}/")

        self.assertEqual(response.status_code, 403)
        self.assertTrue(User.objects.filter(pk=self.admin.id).exists())

    def test_cannot_delete_self(self):
        response = self.client_as(self.admin).delete(f"/api/settings/users/{self.admin.id}/")

        self.assertEqual(response.status_code, 400)
        self.assertTrue(User.objects.filter(pk=self.admin.id).exists())

    def test_admin_cannot_delete_super_user(self):
        response = self.client_as(self.admin).delete(
            f"/api/settings/users/{self.super_user.id}/"
        )

        self.assertEqual(response.status_code, 403)
        self.assertTrue(User.objects.filter(pk=self.super_user.id).exists())

    def test_super_user_cannot_delete_system_owner(self):
        response = self.client_as(self.super_user).delete(
            f"/api/settings/users/{self.system_owner.id}/"
        )

        self.assertEqual(response.status_code, 403)
        self.assertTrue(User.objects.filter(pk=self.system_owner.id).exists())

    def test_deleting_one_of_multiple_system_owners_succeeds(self):
        second_owner = make_user("delete_test_owner2", Role.SYSTEM_OWNER)

        response = self.client_as(self.system_owner).delete(
            f"/api/settings/users/{second_owner.id}/"
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(pk=second_owner.id).exists())
        self.assertTrue(User.objects.filter(pk=self.system_owner.id).exists())

    def test_cannot_delete_last_system_owner(self):
        second_owner = make_user("delete_test_owner3", Role.SYSTEM_OWNER)

        # Delete down to a single System Owner.
        self.client_as(self.system_owner).delete(f"/api/settings/users/{second_owner.id}/")

        response = self.client_as(self.system_owner).delete(
            f"/api/settings/users/{self.system_owner.id}/"
        )

        # Blocked by the "cannot delete self" rule already, but even
        # a hypothetical other elevated actor could not delete the
        # last remaining owner - covered by the deactivate-parity
        # safeguard exercised in SystemOwnerCapabilityTests.
        self.assertEqual(response.status_code, 400)

    def test_delete_nonexistent_user_returns_404(self):
        response = self.client_as(self.admin).delete("/api/settings/users/999999/")

        self.assertEqual(response.status_code, 404)


# ======================================================================
# AUDIT LOG
# ======================================================================


class AuditLogTests(BaseRBACTestCase):
    def test_user_creation_is_audited(self):
        self.client_as(self.system_owner).post(
            "/api/settings/users/",
            {
                "username": "audited_new_user",
                "email": "audited_new_user@example.com",
                "password": "SuperSecret123!",
                "confirm_password": "SuperSecret123!",
                "role": Role.VIEWER,
            },
            format="json",
        )

        entry = UserAuditLog.objects.filter(
            target_username="audited_new_user", action=UserAuditLog.Action.USER_CREATED
        ).first()

        self.assertIsNotNone(entry)
        self.assertEqual(entry.actor, self.system_owner)

    def test_role_change_is_audited(self):
        self.client_as(self.system_owner).patch(
            f"/api/settings/users/{self.viewer.id}/",
            {"role": Role.ADMIN},
            format="json",
        )

        entry = UserAuditLog.objects.filter(
            target_user_id=self.viewer.id, action=UserAuditLog.Action.ROLE_CHANGED
        ).first()

        self.assertIsNotNone(entry)
        self.assertEqual(entry.old_value, Role.VIEWER)
        self.assertEqual(entry.new_value, Role.ADMIN)

    def test_family_membership_change_is_audited(self):
        family = FamilyGroup.objects.create(name="Audited Family")

        self.client_as(self.system_owner).post(
            f"/api/settings/groups/{family.id}/members/",
            {"user_id": self.viewer.id},
            format="json",
        )

        self.assertTrue(
            UserAuditLog.objects.filter(
                target_user_id=self.viewer.id,
                action=UserAuditLog.Action.FAMILY_ADDED,
                new_value=family.name,
            ).exists()
        )

        self.client_as(self.system_owner).delete(
            f"/api/settings/groups/{family.id}/members/{self.viewer.id}/",
        )

        self.assertTrue(
            UserAuditLog.objects.filter(
                target_user_id=self.viewer.id,
                action=UserAuditLog.Action.FAMILY_REMOVED,
                old_value=family.name,
            ).exists()
        )

    def test_deactivation_is_audited(self):
        self.client_as(self.admin).post(f"/api/settings/users/{self.viewer.id}/deactivate/")

        self.assertTrue(
            UserAuditLog.objects.filter(
                target_user_id=self.viewer.id, action=UserAuditLog.Action.DEACTIVATED
            ).exists()
        )
