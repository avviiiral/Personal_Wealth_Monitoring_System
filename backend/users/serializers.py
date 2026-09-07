from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.validators import validate_email
from django.db import transaction

from rest_framework import serializers

from .models import FamilyGroup, Role, UserAuditLog, UserProfile, role_rank
from .permissions import (
    assignable_roles_for_create,
    can_change_role,
    can_manage_target_role,
    get_active_family_group_id,
    get_family_group_ids,
    get_role,
    is_admin_or_above,
    is_super_user_or_above,
    is_system_owner,
)

User = get_user_model()


def _log(actor, target_user, action, old_value="", new_value=""):
    UserAuditLog.objects.create(
        actor=actor if getattr(actor, "is_authenticated", False) else None,
        target_user_id=target_user.pk,
        target_username=target_user.username,
        action=action,
        old_value=str(old_value),
        new_value=str(new_value),
    )


# ==================================================================
# SHARED FIELD HELPERS
# ==================================================================


class FamilySummarySerializer(serializers.ModelSerializer):
    class Meta:
        model = FamilyGroup
        fields = ["id", "name"]
        read_only_fields = fields


def _families_payload(obj):
    profile = getattr(obj, "profile", None)

    if profile is None:
        return []

    return FamilySummarySerializer(profile.family_groups.all(), many=True).data


def _active_family_payload(obj):
    profile = getattr(obj, "profile", None)

    if profile is None:
        return None

    active_id = get_active_family_group_id(obj)

    if active_id is None:
        return None

    family = next(
        (f for f in profile.family_groups.all() if f.id == active_id),
        None,
    )

    return FamilySummarySerializer(family).data if family else None


# ==================================================================
# READ SERIALIZERS
# ==================================================================


class UserListSerializer(serializers.ModelSerializer):
    """
    Row shape for the User Management table and for `settings/me/`.

    Never includes the password/password hash.
    """

    role = serializers.SerializerMethodField()
    status = serializers.SerializerMethodField()
    families = serializers.SerializerMethodField()
    active_family = serializers.SerializerMethodField()
    created_by = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            "id",
            "first_name",
            "last_name",
            "username",
            "email",
            "role",
            "status",
            "is_active",
            "last_login",
            "date_joined",
            "families",
            "active_family",
            "created_by",
        ]
        read_only_fields = fields

    def get_role(self, obj) -> str:
        return get_role(obj) or Role.VIEWER

    def get_status(self, obj) -> str:
        return "Active" if obj.is_active else "Inactive"

    def get_families(self, obj):
        return _families_payload(obj)

    def get_active_family(self, obj):
        return _active_family_payload(obj)

    def get_created_by(self, obj):
        profile = getattr(obj, "profile", None)

        if profile is None or profile.created_by_id is None:
            return None

        return profile.created_by.username


class CurrentUserSerializer(UserListSerializer):
    """`GET /api/settings/me/` (and `/api/auth/me/`) - adds
    permission flags the frontend uses for display/navigation
    purposes only (never for authorization - the backend still
    enforces every action independently)."""

    permissions = serializers.SerializerMethodField()

    class Meta(UserListSerializer.Meta):
        fields = UserListSerializer.Meta.fields + ["permissions"]

    def get_permissions(self, obj) -> dict:
        role = get_role(obj) or Role.VIEWER

        assignable = sorted(assignable_roles_for_create(obj), key=role_rank)

        return {
            # Prices
            "can_edit_prices": is_admin_or_above(obj),
            # User management (general + role-specific)
            "can_manage_users": role in (Role.ADMIN, Role.SUPER_USER, Role.SYSTEM_OWNER),
            "can_create_viewer": Role.VIEWER in assignable,
            "can_create_admin": Role.ADMIN in assignable,
            "can_create_super_user": Role.SUPER_USER in assignable,
            "can_create_system_owner": Role.SYSTEM_OWNER in assignable,
            "can_manage_viewer": can_manage_target_role(obj, Role.VIEWER),
            "can_manage_admin": can_manage_target_role(obj, Role.ADMIN),
            "can_manage_super_user": can_manage_target_role(obj, Role.SUPER_USER),
            "can_manage_system_owner": can_manage_target_role(obj, Role.SYSTEM_OWNER),
            "can_change_roles": role in (Role.SUPER_USER, Role.SYSTEM_OWNER),
            "assignable_roles": assignable,
            # Families
            "can_manage_families": is_system_owner(obj),
            "can_view_all_families": is_system_owner(obj),
            "can_assign_multiple_families": is_system_owner(obj),
            # Kept for any lingering 3-role-era callers; semantically
            # now means "can this user grant the Super User role".
            "can_assign_superuser": Role.SUPER_USER in assignable,
        }


# ==================================================================
# CREATE
# ==================================================================


class UserCreateSerializer(serializers.ModelSerializer):
    """
    `POST /api/settings/users/`

    Server-side validated user creation. The requesting user's
    role (from context) determines which roles may be assigned -
    an Admin can never create a Super User, and a Viewer can never
    reach this serializer at all (blocked by the view's
    permission_classes).

    Family assignment (`family_ids`) may only ever be supplied by
    a System Owner - every other role creates accounts with no
    family membership; a System Owner assigns family afterwards.
    """

    password = serializers.CharField(write_only=True, min_length=1)
    confirm_password = serializers.CharField(write_only=True, min_length=1)
    role = serializers.ChoiceField(choices=Role.choices, default=Role.VIEWER)
    is_active = serializers.BooleanField(default=True)
    family_ids = serializers.PrimaryKeyRelatedField(
        source="family_groups",
        queryset=FamilyGroup.objects.all(),
        required=False,
        many=True,
    )

    class Meta:
        model = User
        fields = [
            "id",
            "first_name",
            "last_name",
            "username",
            "email",
            "password",
            "confirm_password",
            "role",
            "is_active",
            "family_ids",
        ]

    def validate_username(self, value):
        value = value.strip()

        if not value:
            raise serializers.ValidationError("Username is required.")

        if User.objects.filter(username__iexact=value).exists():
            raise serializers.ValidationError("A user with this username already exists.")

        return value

    def validate_email(self, value):
        value = value.strip()

        if not value:
            raise serializers.ValidationError("Email is required.")

        try:
            validate_email(value)
        except DjangoValidationError:
            raise serializers.ValidationError("Enter a valid email address.")

        if User.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError("A user with this email already exists.")

        return value

    def validate_role(self, value):
        requesting_user = self.context["request"].user

        if value not in assignable_roles_for_create(requesting_user):
            raise serializers.ValidationError(
                f"You do not have permission to create a {value} account."
            )

        return value

    def validate_family_ids(self, value):
        requesting_user = self.context["request"].user

        if value and not is_system_owner(requesting_user):
            raise serializers.ValidationError(
                "Only a System Owner can assign family membership."
            )

        return value

    def validate(self, attrs):
        password = attrs.get("password")
        confirm_password = attrs.pop("confirm_password", None)

        if password != confirm_password:
            raise serializers.ValidationError(
                {"confirm_password": "Passwords do not match."}
            )

        # Validate against Django's configured password validators
        # (reuses the same policy as the existing change-password flow).
        try:
            validate_password(password)
        except DjangoValidationError as exc:
            raise serializers.ValidationError({"password": exc.messages})

        return attrs

    @transaction.atomic
    def create(self, validated_data):
        request = self.context["request"]
        requesting_user = request.user

        role = validated_data.pop("role", Role.VIEWER)
        password = validated_data.pop("password")
        family_groups = validated_data.pop("family_groups", [])

        user = User(**validated_data)
        user.set_password(password)

        # Keep Django's own is_superuser flag consistent with the
        # PWMS role so Django admin access matches PWMS RBAC.
        user.is_superuser = role == Role.SYSTEM_OWNER
        user.is_staff = role != Role.VIEWER

        user.save()

        # The post_save signal creates a default (VIEWER) profile
        # and, in doing so, caches that profile instance onto
        # `user.profile` (Django's reverse-o2o descriptor caches
        # the related object as soon as it's constructed with
        # `user=user`). Updating the role through a *separate*
        # `UserProfile.objects.update_or_create(...)` query would
        # correctly update the database row, but would leave that
        # stale cached VIEWER instance on `user.profile` - so the
        # very API response returned for this request would still
        # show the old role. Mutating the already-cached object
        # directly keeps the in-memory instance and the database
        # in sync.
        profile = user.profile
        profile.role = role
        profile.created_by = requesting_user

        profile.save(update_fields=["role", "created_by", "updated_at"])

        if family_groups:
            for family in family_groups:
                profile.family_groups.add(family)
                _log(
                    requesting_user, user, UserAuditLog.Action.FAMILY_ADDED,
                    new_value=family.name,
                )

        _log(
            requesting_user, user, UserAuditLog.Action.USER_CREATED,
            new_value=role,
        )

        return user


# ==================================================================
# UPDATE
# ==================================================================


class UserUpdateSerializer(serializers.ModelSerializer):
    """
    `PUT` / `PATCH /api/settings/users/<id>/`

    Handles both:
      - a System Owner/Super User/Admin editing another account, and
      - a user editing their own profile (self-service).

    Privilege-escalation rules are enforced in `validate()`, which
    has access to both the requesting user and the target instance
    via context, so they hold regardless of what the client sends.
    """

    role = serializers.ChoiceField(choices=Role.choices, required=False)
    family_ids = serializers.PrimaryKeyRelatedField(
        source="family_groups",
        queryset=FamilyGroup.objects.all(),
        required=False,
        many=True,
    )

    class Meta:
        model = User
        fields = [
            "id",
            "first_name",
            "last_name",
            "username",
            "email",
            "role",
            "is_active",
            "family_ids",
        ]
        extra_kwargs = {
            "id": {"read_only": True},
            "username": {"required": False},
            "email": {"required": False},
            "is_active": {"required": False},
        }

    def validate_username(self, value):
        value = value.strip()

        if not value:
            raise serializers.ValidationError("Username is required.")

        if User.objects.filter(username__iexact=value).exclude(pk=self.instance.pk).exists():
            raise serializers.ValidationError("A user with this username already exists.")

        return value

    def validate_email(self, value):
        value = value.strip()

        if not value:
            raise serializers.ValidationError("Email is required.")

        try:
            validate_email(value)
        except DjangoValidationError:
            raise serializers.ValidationError("Enter a valid email address.")

        if User.objects.filter(email__iexact=value).exclude(pk=self.instance.pk).exists():
            raise serializers.ValidationError("A user with this email already exists.")

        return value

    def validate(self, attrs):
        request = self.context["request"]
        requesting_user = request.user
        target_user = self.instance
        is_self_edit = requesting_user.pk == target_user.pk

        elevated = is_admin_or_above(requesting_user)

        # ----------------------------------------------------
        # A user with no management privileges may only ever
        # edit their own basic profile fields, and can never
        # touch role/is_active/family on themselves or anyone
        # else.
        # ----------------------------------------------------

        if not elevated:
            if not is_self_edit:
                raise serializers.ValidationError(
                    "You do not have permission to edit other users."
                )

            for locked_field in ("role", "is_active", "family_groups"):
                if locked_field in attrs:
                    raise serializers.ValidationError(
                        {locked_field: "You do not have permission to change this field."}
                    )

            return attrs

        # ----------------------------------------------------
        # Admin / Super User / System Owner editing someone
        # (possibly themselves).
        # ----------------------------------------------------

        current_role = get_role(target_user)

        # A manager may only ever touch accounts their role is
        # permitted to manage - System Owner and Super User
        # accounts are entirely off-limits to Admin, and System
        # Owner accounts are off-limits to Super User, REGARDLESS
        # of which field is being changed (name, email, active
        # status, role, ...). Self-edit is always allowed for
        # basic fields (handled above via is_self_edit not
        # applying here since elevated users reach this branch
        # even when editing themselves - but a self-edit of one's
        # OWN account is never blocked by "can't manage this
        # role").
        if not is_self_edit and not can_manage_target_role(requesting_user, current_role):
            raise serializers.ValidationError(
                "You do not have permission to manage this account."
            )

        # ----------------------------------------------------
        # ROLE
        # ----------------------------------------------------

        new_role = attrs.get("role")

        if new_role is not None:
            if is_self_edit:
                raise serializers.ValidationError(
                    {"role": "You cannot change your own role."}
                )

            if not can_change_role(requesting_user, current_role, new_role):
                raise serializers.ValidationError(
                    {"role": "You do not have permission to assign this role."}
                )

            if (
                current_role == Role.SYSTEM_OWNER
                and new_role != Role.SYSTEM_OWNER
                and UserProfile.is_last_active_system_owner(target_user)
            ):
                raise serializers.ValidationError(
                    {"role": "Cannot remove the last active System Owner."}
                )

        # ----------------------------------------------------
        # ACTIVE STATUS
        # ----------------------------------------------------

        new_active = attrs.get("is_active")

        if new_active is False:
            if is_self_edit:
                raise serializers.ValidationError(
                    {"is_active": "You cannot deactivate your own account."}
                )

            if current_role == Role.SYSTEM_OWNER and UserProfile.is_last_active_system_owner(
                target_user
            ):
                raise serializers.ValidationError(
                    {"is_active": "Cannot deactivate the last active System Owner."}
                )

        # ----------------------------------------------------
        # FAMILY MEMBERSHIP - System Owner only, full stop.
        # ----------------------------------------------------

        if "family_groups" in attrs and not is_system_owner(requesting_user):
            raise serializers.ValidationError(
                {"family_ids": "Only a System Owner can change family membership."}
            )

        return attrs

    @transaction.atomic
    def update(self, instance, validated_data):
        request = self.context["request"]
        requesting_user = request.user

        role = validated_data.pop("role", None)
        family_groups = validated_data.pop("family_groups", None)
        old_active = instance.is_active

        for field, value in validated_data.items():
            setattr(instance, field, value)

        instance.save()

        if "is_active" in validated_data and validated_data["is_active"] != old_active:
            _log(
                requesting_user, instance,
                UserAuditLog.Action.ACTIVATED
                if instance.is_active
                else UserAuditLog.Action.DEACTIVATED,
            )

        if role is not None:
            old_role = get_role(instance)

            instance.is_superuser = role == Role.SYSTEM_OWNER
            instance.is_staff = role != Role.VIEWER
            instance.save(update_fields=["is_superuser", "is_staff"])

            # `instance` was fetched with select_related("profile"),
            # so `instance.profile` is already cached - mutate that
            # cached object directly (see the matching comment in
            # UserCreateSerializer.create()) rather than issuing a
            # separate update_or_create query, which would update
            # the database but leave the stale cached profile (and
            # therefore this request's own response) showing the
            # old role.
            profile = instance.profile
            profile.role = role
            profile.save(update_fields=["role", "updated_at"])

            if old_role != role:
                _log(
                    requesting_user, instance, UserAuditLog.Action.ROLE_CHANGED,
                    old_value=old_role, new_value=role,
                )

        if family_groups is not None:
            profile = instance.profile

            current_ids = set(profile.family_groups.values_list("id", flat=True))
            new_ids = {f.id for f in family_groups}

            added = [f for f in family_groups if f.id not in current_ids]
            removed_ids = current_ids - new_ids

            profile.family_groups.set(family_groups)

            for family in added:
                _log(
                    requesting_user, instance, UserAuditLog.Action.FAMILY_ADDED,
                    new_value=family.name,
                )

            if removed_ids:
                removed_names = FamilyGroup.objects.filter(
                    id__in=removed_ids
                ).values_list("name", flat=True)

                for name in removed_names:
                    _log(
                        requesting_user, instance, UserAuditLog.Action.FAMILY_REMOVED,
                        old_value=name,
                    )

            # If the active family was removed, clear it so the
            # next read recomputes a valid default.
            if profile.active_family_group_id not in new_ids:
                profile.active_family_group = None
                profile.save(update_fields=["active_family_group", "updated_at"])

        return instance


# ==================================================================
# ACTIVE FAMILY (self-service view selector - not a membership change)
# ==================================================================


class ActiveFamilySerializer(serializers.Serializer):
    """
    `POST /api/settings/me/active-family/`

    Lets any authenticated user choose which of their OWN families
    is currently "selected" for scoping the data they see. This is
    a personal view preference, never a membership change - it is
    available to every role and validated to be one of the user's
    own families.
    """

    family_id = serializers.PrimaryKeyRelatedField(
        queryset=FamilyGroup.objects.all(),
        allow_null=True,
    )

    def validate_family_id(self, value):
        request = self.context["request"]

        if value is not None and value.id not in get_family_group_ids(request.user):
            raise serializers.ValidationError("You are not a member of this family.")

        return value

    def save(self):
        request = self.context["request"]
        profile = request.user.profile

        profile.active_family_group = self.validated_data["family_id"]
        profile.save(update_fields=["active_family_group", "updated_at"])

        return profile


# ==================================================================
# ADMIN-INITIATED PASSWORD RESET
# ==================================================================


class AdminPasswordResetSerializer(serializers.Serializer):
    """
    `POST /api/settings/users/<id>/reset-password/`

    A dedicated, secure endpoint for a System Owner/Super
    User/Admin to set a new password for another account - separate
    from the existing self-service `change-password` flow, which
    requires knowing the current password and is unaffected by
    this.
    """

    new_password = serializers.CharField(write_only=True)
    confirm_password = serializers.CharField(write_only=True)

    def validate(self, attrs):
        if attrs["new_password"] != attrs["confirm_password"]:
            raise serializers.ValidationError(
                {"confirm_password": "Passwords do not match."}
            )

        try:
            validate_password(attrs["new_password"], user=self.context.get("target_user"))
        except DjangoValidationError as exc:
            raise serializers.ValidationError({"new_password": exc.messages})

        return attrs


# ==================================================================
# FAMILY GROUPS
# ==================================================================


class FamilyGroupMemberSerializer(serializers.ModelSerializer):
    """A member row within a FamilyGroup's member list."""

    role = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ["id", "first_name", "last_name", "username", "email", "role"]
        read_only_fields = fields

    def get_role(self, obj) -> str:
        return get_role(obj) or Role.VIEWER


class FamilyGroupSerializer(serializers.ModelSerializer):
    """
    `GET /api/settings/groups/`

    Returns each group with its member list, so the frontend can
    render group -> members without a second round trip per group.
    System Owner only (enforced at the view level - family data is
    otherwise scoped to a user's own memberships via `/settings/me/`).
    """

    members = serializers.SerializerMethodField()

    created_by_username = serializers.CharField(
        source="created_by.username", read_only=True, default=None
    )

    class Meta:
        model = FamilyGroup
        fields = ["id", "name", "created_by_username", "created_at", "members"]
        read_only_fields = fields

    def get_members(self, obj):
        members = User.objects.filter(profile__family_groups=obj).order_by("username")

        return FamilyGroupMemberSerializer(members, many=True).data


class FamilyGroupCreateSerializer(serializers.ModelSerializer):
    """`POST /api/settings/groups/` - System Owner only."""

    class Meta:
        model = FamilyGroup
        fields = ["id", "name"]

    def validate_name(self, value):
        value = value.strip()

        if not value:
            raise serializers.ValidationError("Family name is required.")

        return value

    def create(self, validated_data):
        request = self.context["request"]

        return FamilyGroup.objects.create(
            created_by=request.user,
            **validated_data,
        )


class FamilyGroupUpdateSerializer(serializers.ModelSerializer):
    """`PATCH /api/settings/groups/<id>/` - rename only. System Owner only."""

    class Meta:
        model = FamilyGroup
        fields = ["id", "name"]

    def validate_name(self, value):
        value = value.strip()

        if not value:
            raise serializers.ValidationError("Family name is required.")

        return value
