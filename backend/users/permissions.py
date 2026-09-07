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

======================================================================
PERMISSION MATRIX (source of truth - keep in sync with any docs)
======================================================================

Action                          System Owner  Super User  Admin  Viewer
Login                                YES         YES        YES    YES
View permitted portfolio data       YES         YES        YES    YES
Edit own profile                    YES         YES        YES    YES
Edit manual prices                  YES         YES        YES     NO
Create Viewer                       YES         YES        YES     NO
Create Admin                        YES         YES         NO     NO
Create Super User                   YES          NO         NO     NO
Create System Owner                 YES          NO         NO     NO
Change user role                    YES       Limited*  Limited*    NO
Manage Viewer                       YES         YES        YES     NO
Manage Admin                        YES         YES         NO     NO
Manage Super User                   YES          NO         NO     NO
Manage System Owner                 YES          NO         NO     NO
Add family                          YES          NO         NO     NO
Remove family membership            YES          NO         NO     NO
Change family membership            YES          NO         NO     NO
Assign multiple families            YES          NO         NO     NO
View all families                   YES          NO         NO     NO

* "Limited" role-change rules (see `can_change_role`):
    - Super User may only set a target's role to ADMIN or VIEWER,
      and only when the target's CURRENT role is ADMIN or VIEWER.
    - Admin can never change anyone's role (0 allowed transitions);
      Admin's only user-creation power is creating new Viewers.
  Nobody - including System Owner - may change their OWN role
  through the user-update endpoint (privilege-escalation guard;
  see UserUpdateSerializer.validate).
"""

from rest_framework.permissions import BasePermission

from .models import Role, UserProfile, role_rank


# ======================================================================
# ROLE — read helpers
# ======================================================================


def get_role(user) -> str | None:
    """
    Return the business role for `user`, or None if unavailable
    (e.g. anonymous user, or a profile that somehow doesn't exist
    yet - which should not happen once the post_save signal has
    run, but we never want a missing profile to silently grant
    access).
    """

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
    """Admin, Super User, or System Owner (the three roles that
    can manage at least Viewer users and edit manual prices)."""

    return role_rank(get_role(user)) >= role_rank(Role.ADMIN)


def is_super_user_or_above(user) -> bool:
    return role_rank(get_role(user)) >= role_rank(Role.SUPER_USER)


def is_role_at_least(user, minimum_role) -> bool:
    return role_rank(get_role(user)) >= role_rank(minimum_role)


# ======================================================================
# ROLE — write/management helpers
# ======================================================================


def assignable_roles_for_create(user) -> set[str]:
    """
    Roles `user` may assign when CREATING a brand new account.

      System Owner : VIEWER, ADMIN, SUPER_USER, SYSTEM_OWNER
      Super User    : VIEWER, ADMIN
      Admin         : VIEWER
      Viewer        : (none - blocked at the view/permission-class
                       level long before this is consulted)
    """

    role = get_role(user)

    if role == Role.SYSTEM_OWNER:
        return {Role.VIEWER, Role.ADMIN, Role.SUPER_USER, Role.SYSTEM_OWNER}

    if role == Role.SUPER_USER:
        return {Role.VIEWER, Role.ADMIN}

    if role == Role.ADMIN:
        return {Role.VIEWER}

    return set()


def can_change_role(user, target_current_role, target_new_role) -> bool:
    """
    True if `user` may change a target account's role from
    `target_current_role` to `target_new_role`.

    Self-role-change is intentionally NOT covered here - it is
    blocked unconditionally by the caller regardless of role, as a
    privilege-escalation guard (see UserUpdateSerializer.validate).
    """

    if target_current_role == target_new_role:
        return True

    role = get_role(user)

    if role == Role.SYSTEM_OWNER:
        # System Owner may set anyone to any role, EXCEPT that the
        # last active System Owner may never be changed away from
        # SYSTEM_OWNER (guarded separately, by
        # UserProfile.is_last_active_system_owner - not here, since
        # that check needs the actual user instance).
        return True

    if role == Role.SUPER_USER:
        manageable = {Role.ADMIN, Role.VIEWER}

        return target_current_role in manageable and target_new_role in manageable

    # Admin (and anyone below) can never change roles.
    return False


def can_manage_target_role(user, target_role) -> bool:
    """
    True if `user`'s role permits *managing* (edit / activate /
    deactivate / delete / reset password for) an account whose
    role is `target_role`. This does not check family scope - see
    get_manageable_users_queryset for the combined check used by
    the user list/detail endpoints.
    """

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
    """IDs of every family `user` belongs to (order-insensitive)."""

    profile = getattr(user, "profile", None)

    if profile is None:
        return []

    return list(profile.family_groups.values_list("id", flat=True))


def get_active_family_group_id(user):
    """
    The family currently "selected" for scoping `user`'s own data
    views. Falls back to the lowest-ID family they belong to if no
    active family is set, or if the previously-active one is no
    longer one of their families (e.g. System Owner removed them
    from it). Returns None if the user belongs to no family.
    """

    profile = getattr(user, "profile", None)

    if profile is None:
        return None

    family_ids = get_family_group_ids(user)

    if not family_ids:
        return None

    if profile.active_family_group_id in family_ids:
        return profile.active_family_group_id

    return min(family_ids)


def get_visible_owner_ids(user) -> list[int]:
    """
    IDs of the users whose portfolio data `user` may VIEW.

    - System Owner: every user in the system (role grants
      unrestricted view access across all families - "See all
      portfolio data across families").
    - Everyone else: themselves, plus every other member of their
      CURRENTLY SELECTED (active) family only. A user belonging to
      multiple families is deliberately NOT shown a combined view
      of all of them at once - they must select which family they
      are viewing (see get_active_family_group_id /
      UserProfile.active_family_group). A user in no family sees
      only themselves.
    """

    from django.contrib.auth import get_user_model

    if is_system_owner(user):
        User = get_user_model()

        return list(User.objects.values_list("id", flat=True))

    active_family_id = get_active_family_group_id(user)

    if active_family_id is None:
        return [user.id]

    member_ids = set(
        UserProfile.objects
        .filter(family_groups__id=active_family_id)
        .values_list("user_id", flat=True)
    )

    member_ids.add(user.id)

    return list(member_ids)


def get_manageable_users_queryset(user):
    """
    The set of User accounts `user` may LIST/VIEW/MANAGE in the
    User Management screens.

    Scoped by ROLE ONLY. Family membership never gates account
    management (viewing the list, editing, activating/
    deactivating, resetting a password, or deleting) - it only
    gates which family's PORTFOLIO DATA a user can see (see
    get_visible_owner_ids). Mixing the two would mean a role's
    documented capability (e.g. Admin's unconditional "Manage
    Viewer") silently stops working just because nobody has set up
    a shared family yet, which contradicts "never infer role
    permissions from family membership."

      System Owner : every user, any role.
      Super User    : users with role ADMIN or VIEWER, plus
                       themselves.
      Admin         : users with role VIEWER, plus themselves.
      Viewer        : themselves only.
    """

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
    """User is authenticated and has (at least) the Viewer role."""

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
    """
    User is Admin, Super User, or System Owner - the roles that
    can manage at least Viewer users and edit manual prices.

    Name kept from the previous 3-role model for minimal call-site
    churn; semantics now cover all three non-Viewer roles.
    """

    message = "This action requires Admin, Super User, or System Owner privileges."

    def has_permission(self, request, view):
        return is_admin_or_above(request.user)
