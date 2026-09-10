"""
Centralized role + family authorization for PWMS.

This module is the single source of truth for authorization
decisions on the backend. Nothing here trusts a role or family ID
claimed by the client - every check re-derives the requesting
user's role/family memberships from the database on every request.

Two concepts are combined here, and kept deliberately separate:

  - ROLE determines what a user is allowed to DO (capabilities).
  - FAMILY MEMBERSHIP determines what family data a user can SEE.

Never infer one from the other. A helper that looks like it mixes
them (e.g. get_manageable_users_queryset) always documents exactly
how it combines them.
"""

from rest_framework.permissions import BasePermission

from .models import Role, UserProfile, role_rank


# ======================================================================
# ROLE — read helpers
# ======================================================================


def get_role(user) -> str | None:
    """Return the business role for `user`, or None if unavailable."""

    if not getattr(user, "is_authenticated", False):
        return None

    profile = getattr(user, "profile", None)

    if profile is None:
        return None

    return profile.role


def is_viewer(user) -> bool:
    return get_role(user) == Role.VIEWER


def is_admin(user) -> bool:
    return get_role(user) == Role.ADMIN


def is_super_user(user) -> bool:
    return get_role(user) == Role.SUPER_USER


def is_system_owner(user) -> bool:
    return get_role(user) == Role.SYSTEM_OWNER


def is_admin_or_above(user) -> bool:
    return role_rank(get_role(user)) >= role_rank(Role.ADMIN)


def is_super_user_or_above(user) -> bool:
    return role_rank(get_role(user)) >= role_rank(Role.SUPER_USER)


def is_role_at_least(user, minimum_role) -> bool:
    return role_rank(get_role(user)) >= role_rank(minimum_role)


# ======================================================================
# ROLE — write/management helpers
# ======================================================================


def assignable_roles_for_create(user) -> set[str]:
    role = get_role(user)

    if role == Role.SYSTEM_OWNER:
        return {Role.VIEWER, Role.ADMIN, Role.SUPER_USER, Role.SYSTEM_OWNER}

    if role == Role.SUPER_USER:
        return {Role.VIEWER, Role.ADMIN}

    if role == Role.ADMIN:
        return {Role.VIEWER}

    return set()


def can_change_role(user, target_current_role, target_new_role) -> bool:
    if target_current_role == target_new_role:
        return True

    role = get_role(user)

    if role == Role.SYSTEM_OWNER:
        return True

    if role == Role.SUPER_USER:
        manageable = {Role.ADMIN, Role.VIEWER}
        return target_current_role in manageable and target_new_role in manageable

    return False


def can_manage_target_role(user, target_role) -> bool:
    role = get_role(user)

    if role == Role.SYSTEM_OWNER:
        return True

    if role == Role.SUPER_USER:
        return target_role in (Role.ADMIN, Role.VIEWER)

    if role == Role.ADMIN:
        return target_role == Role.VIEWER

    return False


# ======================================================================
# FAMILY — read helpers
# ======================================================================


def get_family_group_ids(user) -> list[int]:
    """IDs of every family `user` belongs to."""

    profile = getattr(user, "profile", None)

    if profile is None:
        return []

    return list(profile.family_groups.values_list("id", flat=True))


def get_active_family_group_id(user):
    """
    Return the explicitly selected family for `user`.

    There is intentionally NO fallback to another family. A user's
    financial-data scope must never silently change because the
    active family is missing or invalid.

    Returns None when:
      - the user/profile does not exist,
      - the user belongs to no families, or
      - no valid active family is explicitly selected.

    Callers that access family-owned financial data must treat None
    as "no active family" rather than falling back to user ownership
    or another family.
    """

    profile = getattr(user, "profile", None)

    if profile is None:
        return None

    active_family_id = profile.active_family_group_id

    if active_family_id is None:
        return None

    if not profile.family_groups.filter(pk=active_family_id).exists():
        return None

    return active_family_id


def get_visible_owner_ids(user) -> list[int]:
    """
    Legacy compatibility helper.

    Financial-data isolation is being migrated to FamilyGroup. New
    financial-data code must use get_active_family_group_id() and
    filter directly by family_group_id. This helper remains only for
    existing call sites during the staged migration.
    """

    from django.contrib.auth import get_user_model

    if is_system_owner(user):
        User = get_user_model()
        return list(User.objects.values_list("id", flat=True))

    active_family_id = get_active_family_group_id(user)

    if active_family_id is None:
        return []

    member_ids = set(
        UserProfile.objects
        .filter(family_groups__id=active_family_id)
        .values_list("user_id", flat=True)
    )

    return list(member_ids)


def get_manageable_users_queryset(user):
    """Return the User accounts `user` may manage, based on role only."""

    from django.contrib.auth import get_user_model
    from django.db.models import Q

    User = get_user_model()
    role = get_role(user)

    if role == Role.SYSTEM_OWNER:
        return User.objects.all()

    if role == Role.SUPER_USER:
        manageable_roles = [Role.ADMIN, Role.VIEWER]
    elif role == Role.ADMIN:
        manageable_roles = [Role.VIEWER]
    else:
        return User.objects.filter(pk=user.pk)

    scoped = Q(profile__role__in=manageable_roles)

    return (User.objects.filter(scoped) | User.objects.filter(pk=user.pk)).distinct()


# ======================================================================
# DRF PERMISSION CLASSES
# ======================================================================


class IsViewer(BasePermission):
    """User is authenticated and has a PWMS business role."""

    message = "You must be logged in to access this resource."

    def has_permission(self, request, view):
        return get_role(request.user) is not None


class IsAdmin(BasePermission):
    """User's role is exactly Admin."""

    message = "This action requires Admin privileges."

    def has_permission(self, request, view):
        return is_admin(request.user)


class IsSuperUser(BasePermission):
    """User's role is exactly Super User."""

    message = "This action requires Super User privileges."

    def has_permission(self, request, view):
        return is_super_user(request.user)


class IsSystemOwner(BasePermission):
    """User's role is System Owner."""

    message = "This action requires System Owner privileges."

    def has_permission(self, request, view):
        return is_system_owner(request.user)


class IsAdminOrSuperUser(BasePermission):
    """User is Admin, Super User, or System Owner."""

    message = "This action requires Admin, Super User, or System Owner privileges."

    def has_permission(self, request, view):
        return is_admin_or_above(request.user)
