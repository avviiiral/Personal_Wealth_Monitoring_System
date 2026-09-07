from django.conf import settings
from django.db import models


class UserPreference(models.Model):
    """
    Stores application preferences for a PWMS user.
    """

    CURRENCY_CHOICES = [
        ("INR", "Indian Rupee"),
        ("USD", "US Dollar"),
        ("EUR", "Euro"),
        ("GBP", "British Pound"),
    ]

    DATE_FORMAT_CHOICES = [
        ("DD MMM YYYY", "12 Aug 2026"),
        ("DD/MM/YYYY", "12/08/2026"),
        ("YYYY-MM-DD", "2026-08-12"),
    ]

    ANALYTICS_PERIOD_CHOICES = [
        (30, "30 Days"),
        (90, "90 Days"),
        (180, "180 Days"),
        (365, "1 Year"),
    ]

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="preferences",
    )

    currency = models.CharField(
        max_length=3,
        choices=CURRENCY_CHOICES,
        default="INR",
    )

    date_format = models.CharField(
        max_length=20,
        choices=DATE_FORMAT_CHOICES,
        default="DD MMM YYYY",
    )

    default_analytics_period = models.PositiveIntegerField(
        choices=ANALYTICS_PERIOD_CHOICES,
        default=30,
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    updated_at = models.DateTimeField(
        auto_now=True,
    )

    def __str__(self):
        return f"Preferences - {self.user.username}"


# ==============================================================
# ROLE-BASED ACCESS CONTROL (RBAC)
# ==============================================================
#
# PWMS supports exactly four hierarchical roles. The role is the
# single source of truth for *capability* authorization decisions
# across the backend - the frontend never determines permissions
# on its own, it only mirrors what the backend reports.
#
#   VIEWER        - read-only access to the application.
#   ADMIN         - operational management: Viewer users and
#                   manual prices.
#   SUPER_USER    - highest operational role below System Owner:
#                   Admin + Viewer users, manual prices, full
#                   portfolio functionality.
#   SYSTEM_OWNER  - unrestricted application-level authority:
#                   every user, every role, every family. Maps
#                   1:1 with Django's built-in `is_superuser` flag
#                   so that Django admin / manage.py
#                   createsuperuser keep working exactly as
#                   before (see users/signals.py).
#
# Role hierarchy (low -> high):
#
#   VIEWER  <  ADMIN  <  SUPER_USER  <  SYSTEM_OWNER
#
# Family membership is a SEPARATE concept from role - it controls
# which family's data a user can VIEW, never what actions they are
# allowed to perform. See FamilyGroup / FamilyMembership below and
# users/permissions.py, which is the single place both concepts
# are combined into actual authorization decisions.
#
# NOTE ON THE STORED VALUE FOR "Super User": the underlying string
# stored in the database is intentionally left as "SUPERUSER" -
# this was the value used by the previous 3-role model's top role
# (which a data migration remaps to SYSTEM_OWNER, see
# users/migrations/0008_promote_legacy_superuser_to_system_owner.py).
# Reusing the string for the *new*, second-highest role avoids a
# needless value-rewrite migration; only the Python-level name and
# human label changed.


class Role(models.TextChoices):
    VIEWER = "VIEWER", "Viewer"
    ADMIN = "ADMIN", "Admin"
    SUPER_USER = "SUPERUSER", "Super User"
    SYSTEM_OWNER = "SYSTEM_OWNER", "System Owner"


# Ascending order of authority. Index = rank; higher rank = more
# authority. Central to every "is at least as senior as" check.
ROLE_ORDER = [Role.VIEWER, Role.ADMIN, Role.SUPER_USER, Role.SYSTEM_OWNER]


def role_rank(role) -> int:
    """Numeric rank of `role` (0 = Viewer .. 3 = System Owner).

    Unknown/None values rank below Viewer so they never
    accidentally satisfy an "at least X" check.
    """

    try:
        return ROLE_ORDER.index(role)
    except ValueError:
        return -1


class UserProfile(models.Model):
    """
    Extends the built-in Django User with the PWMS business role
    and family membership.

    Every user has exactly one UserProfile (created automatically
    via a signal - see users/signals.py). Django's `is_superuser`
    is kept in sync with role == SYSTEM_OWNER so that Django admin
    access and PWMS's own RBAC never disagree with each other.
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="profile",
    )

    role = models.CharField(
        max_length=20,
        choices=Role.choices,
        default=Role.VIEWER,
    )

    # --------------------------------------------------------
    # FAMILY MEMBERSHIP (many-to-many)
    # --------------------------------------------------------
    #
    # A user may belong to zero, one, or many families at the same
    # time. Membership is a pure *visibility* grant - members of a
    # family can VIEW each other's portfolio data (Dashboard,
    # Portfolio, Analytics, Mutual Funds/SIPs) for that family. It
    # never implies edit rights, which stay governed entirely by
    # role and actual resource ownership.
    #
    # Only a System Owner may change these relationships (add,
    # remove, move, or multi-assign) - see users/permissions.py
    # and users/api_views.py, which are the sole write paths.

    family_groups = models.ManyToManyField(
        "FamilyGroup",
        through="FamilyMembership",
        related_name="members",
        blank=True,
        help_text=(
            "Families this user belongs to. Members of the same "
            "family can view each other's portfolio data for that "
            "family; membership never affects edit permissions, "
            "which stay governed by role and actual resource "
            "ownership. Only a System Owner may change this."
        ),
    )



    # Which of the user's own families is currently "selected" for
    # scoping the data they see (Dashboard/Portfolio/Analytics/
    # Mutual Funds). This is a personal view preference, not a
    # membership change - any user may switch it among their own
    # families at any time. A user with multiple families is
    # intentionally NOT shown a combined/merged view by default;
    # see users/permissions.get_visible_owner_ids.
    active_family_group = models.ForeignKey(
        "FamilyGroup",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        help_text=(
            "The family currently selected for scoping this user's "
            "own data views. Must be one of the user's own "
            "family_groups; not itself a membership change."
        ),
    )

    # Audit: who created this account. Nullable because the very
    # first System Owner (e.g. via manage.py createsuperuser) has
    # no creator.
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="users_created",
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    updated_at = models.DateTimeField(
        auto_now=True,
    )

    class Meta:
        ordering = ["user__username"]

    def __str__(self):
        return f"{self.user.username} - {self.role}"

    @property
    def is_viewer(self) -> bool:
        return self.role == Role.VIEWER

    @property
    def is_admin(self) -> bool:
        return self.role == Role.ADMIN

    @property
    def is_super_user(self) -> bool:
        return self.role == Role.SUPER_USER

    @property
    def is_system_owner(self) -> bool:
        return self.role == Role.SYSTEM_OWNER

    @staticmethod
    def is_last_active_system_owner(user) -> bool:
        """
        True if `user` is currently the system's only active
        System Owner. Used to block operations (deactivate,
        demote, delete) that would leave PWMS with no System Owner
        at all and therefore nobody able to administer it.
        """

        if not user.is_active:
            return False

        profile = getattr(user, "profile", None)

        if profile is None or profile.role != Role.SYSTEM_OWNER:
            return False

        count = UserProfile.objects.filter(
            role=Role.SYSTEM_OWNER,
            user__is_active=True,
        ).count()

        return count <= 1


# ==============================================================
# FAMILIES (shared data visibility)
# ==============================================================
#
# A FamilyGroup is an opt-in visibility boundary: every member of
# a group can VIEW every other member's portfolio data (Dashboard,
# Portfolio, Analytics, Mutual Funds/SIPs) *for that family*. It
# does NOT change who can EDIT what - manual price overrides and
# any other write action remain scoped to the actual resource
# owner and governed by the existing Role permissions.
#
# A user may belong to any number of families simultaneously (see
# UserProfile.family_groups above). Only a System Owner may create
# families or change membership; every other role only ever reads
# family data for families it already belongs to.


class FamilyGroup(models.Model):
    name = models.CharField(
        max_length=100,
    )

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="family_groups_created",
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class FamilyMembership(models.Model):
    """
    Explicit through model for UserProfile <-> FamilyGroup, so
    membership changes can be audited (who added/removed whom,
    and when) without bolting that metadata onto UserProfile
    itself.
    """

    profile = models.ForeignKey(
        UserProfile,
        on_delete=models.CASCADE,
        related_name="family_memberships",
    )

    family_group = models.ForeignKey(
        FamilyGroup,
        on_delete=models.CASCADE,
        related_name="family_memberships",
    )

    added_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        help_text="Who granted this membership (always a System Owner).",
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["profile", "family_group"],
                name="unique_profile_family_membership",
            ),
        ]
        ordering = ["family_group__name", "profile__user__username"]

    def __str__(self):
        return f"{self.profile.user.username} in {self.family_group.name}"


# ==============================================================
# AUDIT LOG
# ==============================================================
#
# A minimal, append-only trail for security-sensitive user
# administration actions: user creation, role changes, family
# membership changes, and activate/deactivate. This is separate
# from (and does not replace) the existing manual-price audit
# trail in market_data.MarketPrice, which is untouched.


class UserAuditLog(models.Model):
    class Action(models.TextChoices):
        USER_CREATED = "USER_CREATED", "User created"
        ROLE_CHANGED = "ROLE_CHANGED", "Role changed"
        FAMILY_ADDED = "FAMILY_ADDED", "Added to family"
        FAMILY_REMOVED = "FAMILY_REMOVED", "Removed from family"
        ACTIVATED = "ACTIVATED", "Account activated"
        DEACTIVATED = "DEACTIVATED", "Account deactivated"
        DELETED = "DELETED", "Account deleted"

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        help_text="The user who performed the action.",
    )

    # Not a FK: the target user may since have been deleted, and
    # the audit trail should survive that.
    target_user_id = models.PositiveIntegerField()

    target_username = models.CharField(max_length=150)

    action = models.CharField(
        max_length=20,
        choices=Action.choices,
    )

    old_value = models.CharField(max_length=255, blank=True, default="")

    new_value = models.CharField(max_length=255, blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.action} - {self.target_username} @ {self.created_at:%Y-%m-%d %H:%M}"
