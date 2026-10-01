from decimal import Decimal, InvalidOperation

from django.db import IntegrityError
from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models.deletion import ProtectedError

from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from investments.models import Asset

from .models import FamilyGroup, Role, TaxRateChangeLog, TaxRateSetting, UserAuditLog, UserProfile
from .permissions import (
    IsAdminOrSuperUser,
    IsSystemOwner,
    get_manageable_users_queryset,
    get_role,
    is_admin_or_above,
    require_active_family,
)
from .serializers import (
    ActiveFamilySerializer,
    AdminPasswordResetSerializer,
    CurrentUserSerializer,
    FamilyGroupCreateSerializer,
    FamilyGroupSerializer,
    FamilyGroupUpdateSerializer,
    UserCreateSerializer,
    UserListSerializer,
    UserUpdateSerializer,
)

User = get_user_model()


def _get_target_user(user_id):
    try:
        return User.objects.select_related("profile").get(pk=user_id)
    except User.DoesNotExist:
        return None


def _can_manage_user(requesting_user, target_user) -> bool:
    """
    Role-scope check: can `requesting_user` view/manage
    `target_user` through the User Management screens? See
    users.permissions.get_manageable_users_queryset for the exact
    rules (always True for target_user == requesting_user; this
    never depends on family membership - see that function's
    docstring for why).
    """

    return get_manageable_users_queryset(requesting_user).filter(pk=target_user.pk).exists()


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
# CURRENT USER
# ==================================================================


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def current_user_settings(request):
    """
    GET /api/settings/me/

    Account/profile information for the authenticated user,
    including their role, family memberships, currently active
    family, and derived permission flags.
    """

    serializer = CurrentUserSerializer(request.user)

    return Response(serializer.data)

@api_view(["POST"])
@permission_classes([IsAuthenticated])
def set_active_family(request):
    """
    POST /api/settings/me/active-family/

    Body: {"family_id": <id> | null}

    Lets the authenticated user choose which of their OWN families
    is currently selected for scoping Dashboard/Portfolio/
    Analytics/Mutual Fund data. Available to every role - this is
    a personal view preference, never a family membership change,
    and is validated to be one of the user's own families.
    """

    serializer = ActiveFamilySerializer(data=request.data, context={"request": request})

    if not serializer.is_valid():
        return Response({"detail": serializer.errors}, status=status.HTTP_400_BAD_REQUEST)

    serializer.save()

    return Response(CurrentUserSerializer(request.user).data)


# ==================================================================
# TAX RATE SETTINGS
# ==================================================================


def _tax_values(row):
    if row is None:
        return {
            "tenure_months": None,
            "short_term_tax_rate": None,
            "long_term_tax_rate": None,
        }
    return {
        "tenure_months": row.tenure_months,
        "short_term_tax_rate": (
            str(row.short_term_tax_rate)
            if row.short_term_tax_rate is not None
            else None
        ),
        "long_term_tax_rate": (
            str(row.long_term_tax_rate)
            if row.long_term_tax_rate is not None
            else None
        ),
    }


def _log_tax_change(user, family, asset, before, after):
    TaxRateChangeLog.objects.create(
        user=user,
        username=user.username,
        family=family,
        family_name=family.name,
        asset=asset,
        asset_name=asset.name,
        change_from=_tax_values(before),
        change_to=_tax_values(after),
    )


@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated])
def tax_rate_list(request):
    """GET/POST /api/settings/tax-rates/ for the active family.

    Tax configuration is keyed by Asset Name within the family. If
    the same Asset Name exists on multiple Asset records, it is shown
    once and the saved tax configuration is applied consistently to
    all matching records.
    """

    family = require_active_family(request.user)

    if request.method == "GET":
        assets = list(
            Asset.objects
            .filter(family=family, is_active=True)
            .order_by("name", "id")
        )

        # One row per Asset Name, never per underlying/security record.
        first_asset_by_name = {}
        for asset in assets:
            first_asset_by_name.setdefault(asset.name, asset)

        asset_names = list(first_asset_by_name.keys())
        settings_by_name = {}
        for row in TaxRateSetting.objects.filter(
            family=family,
            asset__name__in=asset_names,
        ).select_related("asset").order_by("asset__name", "-updated_at"):
            settings_by_name.setdefault(row.asset.name, row)

        return Response([
            {
                "id": row.id if row else None,
                "asset_id": asset.id,
                "asset_name": asset.name,
                "family_id": family.id,
                "family_name": family.name,
                "tenure_months": row.tenure_months if row else None,
                "short_term_tax_rate": (
                    str(row.short_term_tax_rate)
                    if row and row.short_term_tax_rate is not None
                    else None
                ),
                "long_term_tax_rate": (
                    str(row.long_term_tax_rate)
                    if row and row.long_term_tax_rate is not None
                    else None
                ),
                "updated_at": row.updated_at if row else None,
            }
            for asset in first_asset_by_name.values()
            for row in [settings_by_name.get(asset.name)]
        ])

    asset_id = request.data.get("asset_id")
    try:
        asset_id = int(asset_id)
    except (TypeError, ValueError):
        return Response({"detail": "Asset is required."}, status=status.HTTP_400_BAD_REQUEST)

    asset = Asset.objects.filter(
        pk=asset_id,
        family=family,
        is_active=True,
    ).first()
    if asset is None:
        return Response(
            {"detail": "Asset is not part of the active family."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    tenure_months = request.data.get("tenure_months")
    if tenure_months in ("", None):
        return Response(
            {"detail": "Tenure is required and must be in months."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    try:
        tenure_months = int(tenure_months)
    except (TypeError, ValueError):
        return Response({"detail": "Tenure must be a whole number of months."}, status=status.HTTP_400_BAD_REQUEST)
    if tenure_months < 0:
        return Response({"detail": "Tenure cannot be negative."}, status=status.HTTP_400_BAD_REQUEST)

    def parse_tax_rate(field_name):
        value = request.data.get(field_name)
        if value in ("", None):
            raise ValueError(f"{field_name} is required.")
        try:
            parsed = Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError):
            raise ValueError(f"{field_name} must be a valid number.")
        if parsed < 0 or parsed > 100:
            raise ValueError(f"{field_name} must be between 0 and 100 percent.")
        return parsed

    try:
        short_term_tax_rate = parse_tax_rate("short_term_tax_rate")
        long_term_tax_rate = parse_tax_rate("long_term_tax_rate")
    except ValueError as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

    matching_assets = list(
        Asset.objects.filter(
            family=family,
            name=asset.name,
            is_active=True,
        ).order_by("id")
    )
    existing_rows = list(
        TaxRateSetting.objects.filter(
            family=family,
            asset__in=matching_assets,
        ).select_related("asset")
    )
    before = next((row for row in existing_rows if row.asset_id == asset.id), existing_rows[0] if existing_rows else None)

    if existing_rows:
        TaxRateSetting.objects.filter(pk__in=[row.pk for row in existing_rows]).update(
            tenure_months=tenure_months,
            short_term_tax_rate=short_term_tax_rate,
            long_term_tax_rate=long_term_tax_rate,
        )
        row = TaxRateSetting.objects.get(pk=before.pk)
        created = False
    else:
        row = TaxRateSetting.objects.create(
            family=family,
            asset=asset,
            tenure_months=tenure_months,
            short_term_tax_rate=short_term_tax_rate,
            long_term_tax_rate=long_term_tax_rate,
        )
        created = True

    _log_tax_change(request.user, family, asset, before, row)

    return Response(
        {
            "id": row.id,
            "asset_id": asset.id,
            "asset_name": asset.name,
            "family_id": family.id,
            "family_name": family.name,
            "tenure_months": row.tenure_months,
            "short_term_tax_rate": str(row.short_term_tax_rate),
            "long_term_tax_rate": str(row.long_term_tax_rate),
            "updated_at": row.updated_at,
        },
        status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
    )


@api_view(["PATCH", "DELETE"])
@permission_classes([IsAuthenticated])
def tax_rate_detail(request, tax_rate_id):
    """PATCH/DELETE /api/settings/tax-rates/<id>/ for the active family."""

    family = require_active_family(request.user)

    try:
        row = TaxRateSetting.objects.select_related("asset").get(
            pk=tax_rate_id,
            family=family,
        )
    except TaxRateSetting.DoesNotExist:
        return Response({"detail": "Tax setting not found."}, status=status.HTTP_404_NOT_FOUND)

    matching_rows = TaxRateSetting.objects.filter(
        family=family,
        asset__name=row.asset.name,
    ).select_related("asset")

    if request.method == "DELETE":
        before = row
        _log_tax_change(request.user, family, row.asset, before, None)
        matching_rows.delete()
        return Response({"message": "Tax setting cleared successfully."})

    updates = {}

    if "tenure_months" in request.data:
        try:
            tenure_months = int(request.data["tenure_months"])
        except (TypeError, ValueError):
            return Response({"detail": "Tenure must be a whole number of months."}, status=status.HTTP_400_BAD_REQUEST)
        if tenure_months < 0:
            return Response({"detail": "Tenure cannot be negative."}, status=status.HTTP_400_BAD_REQUEST)
        updates["tenure_months"] = tenure_months

    for field_name in ("short_term_tax_rate", "long_term_tax_rate"):
        if field_name in request.data:
            try:
                value = Decimal(str(request.data[field_name]))
            except (InvalidOperation, TypeError, ValueError):
                return Response({"detail": f"{field_name} must be a valid number."}, status=status.HTTP_400_BAD_REQUEST)
            if value < 0 or value > 100:
                return Response({"detail": f"{field_name} must be between 0 and 100 percent."}, status=status.HTTP_400_BAD_REQUEST)
            updates[field_name] = value

    if not updates:
        return Response({"detail": "No tax settings were supplied."}, status=status.HTTP_400_BAD_REQUEST)

    before = TaxRateSetting.objects.get(pk=row.pk)

    for field_name, value in updates.items():
        setattr(row, field_name, value)

    row.save(update_fields=[*updates.keys(), "updated_at"])

    # Keep duplicate Asset records with the same Asset Name synchronized.
    duplicate_ids = list(matching_rows.values_list("id", flat=True))
    if duplicate_ids:
        TaxRateSetting.objects.filter(pk__in=duplicate_ids).update(
            **updates,
        )

    _log_tax_change(request.user, family, row.asset, before, row)

    return Response(
        {
            "id": row.id,
            "asset_id": row.asset_id,
            "asset_name": row.asset.name,
            "family_id": family.id,
            "family_name": family.name,
            "tenure_months": row.tenure_months,
            "short_term_tax_rate": str(row.short_term_tax_rate),
            "long_term_tax_rate": str(row.long_term_tax_rate),
            "updated_at": row.updated_at,
        }
    )


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def tax_rate_change_history(request):
    """GET /api/settings/tax-rates/history/ for the active family."""

    family = require_active_family(request.user)
    rows = (
        TaxRateChangeLog.objects
        .filter(family=family)
        .order_by("-changed_at", "-id")
    )

    return Response([
        {
            "id": row.id,
            "user": row.username,
            "date_time": row.changed_at,
            "asset_name": row.asset_name,
            "change_from": row.change_from,
            "change_to": row.change_to,
        }
        for row in rows
    ])



@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated, IsAdminOrSuperUser])
def user_list(request):
    """
    GET  /api/settings/users/   - list users this requester may
                                    manage (System Owner sees
                                    everyone; Super User/Admin see
                                    only users within their manage-
                                    able role range who share a
                                    family with them or were
                                    created by them, plus
                                    themselves).
    POST /api/settings/users/   - create a new user, role limited
                                    to what the requester may
                                    assign; family membership may
                                    only be set by a System Owner.
    """

    if request.method == "GET":
        users = (
            get_manageable_users_queryset(request.user)
            .select_related("profile")
            .prefetch_related("profile__family_groups")
            .order_by("username")
        )

        serializer = UserListSerializer(users, many=True)

        return Response(serializer.data)

    # POST - create user
    serializer = UserCreateSerializer(data=request.data, context={"request": request})

    if not serializer.is_valid():
        return Response(
            {"detail": serializer.errors},
            status=status.HTTP_400_BAD_REQUEST,
        )

    user = serializer.save()

    return Response(
        UserListSerializer(user).data,
        status=status.HTTP_201_CREATED,
    )


# ==================================================================
# USER DETAIL / UPDATE
# ==================================================================



@api_view(["GET", "PUT", "PATCH", "DELETE"])
@permission_classes([IsAuthenticated])
def user_detail(request, user_id):
    """
    GET    /api/settings/users/<id>/
    PUT    /api/settings/users/<id>/
    PATCH  /api/settings/users/<id>/
    DELETE /api/settings/users/<id>/

    A System Owner/Super User/Admin can view, edit, or delete any
    account within their manageable role scope (subject
    to the privilege-escalation rules enforced in
    UserUpdateSerializer, and the deletion safeguards below). Any
    other authenticated user may only view/edit their own account,
    and only non-privileged fields.
    """

    target_user = _get_target_user(user_id)

    if target_user is None:
        return Response(
            {"detail": "User not found."},
            status=status.HTTP_404_NOT_FOUND,
        )

    is_self = request.user.pk == target_user.pk
    can_manage = _can_manage_user(request.user, target_user)

    if not is_self and not can_manage:
        return Response(
            {"detail": "You do not have permission to access this user."},
            status=status.HTTP_403_FORBIDDEN,
        )

    if request.method == "GET":
        return Response(UserListSerializer(target_user).data)

    if request.method == "DELETE":
        return _delete_user(request, target_user)

    partial = request.method == "PATCH"

    serializer = UserUpdateSerializer(
        target_user,
        data=request.data,
        partial=partial,
        context={"request": request},
    )

    if not serializer.is_valid():
        return Response(
            {"detail": serializer.errors},
            status=status.HTTP_400_BAD_REQUEST,
        )

    user = serializer.save()

    return Response(UserListSerializer(user).data)


def _delete_user(request, target_user):
    """
    Permanently delete `target_user`.

    Requires System Owner/Super User/Admin privileges AND that the
    target is within the requester's manageable role scope
    (already checked by the caller before reaching here).
    Nobody can delete themselves, and the system's last active
    System Owner can never be deleted, mirroring the deactivate
    safeguards.

    Deleting a user cascades to everything that FK's to them with
    on_delete=CASCADE (their Assets, Transactions, SIPs, etc. -
    exactly the same cascade Django would already apply via the
    existing model definitions). This is destructive and
    irreversible, so the frontend requires an explicit typed
    confirmation before calling this endpoint.
    """

    if not is_admin_or_above(request.user):
        return Response(
            {"detail": "This action requires elevated privileges."},
            status=status.HTTP_403_FORBIDDEN,
        )

    if target_user.pk == request.user.pk:
        return Response(
            {"detail": "You cannot delete your own account."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    target_role = get_role(target_user)

    if target_role == Role.SYSTEM_OWNER and UserProfile.is_last_active_system_owner(target_user):
        return Response(
            {"detail": "Cannot delete the last active System Owner."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    username = target_user.username

    with transaction.atomic():
        _log(
            request.user,
            target_user,
            UserAuditLog.Action.DELETED,
            old_value=target_role or "",
        )

        target_user.delete()

    return Response({"message": f'User "{username}" deleted successfully.'})


# ==================================================================
# ACTIVATE / DEACTIVATE
# ==================================================================


@api_view(["POST"])
@permission_classes([IsAuthenticated, IsAdminOrSuperUser])
def activate_user(request, user_id):
    """POST /api/settings/users/<id>/activate/"""

    target_user = _get_target_user(user_id)

    if target_user is None:
        return Response(
            {"detail": "User not found."},
            status=status.HTTP_404_NOT_FOUND,
        )

    if not _can_manage_user(request.user, target_user):
        return Response(
            {"detail": "You do not have permission to manage this user."},
            status=status.HTTP_403_FORBIDDEN,
        )

    already_active = target_user.is_active

    target_user.is_active = True
    target_user.save(update_fields=["is_active"])

    if not already_active:
        _log(request.user, target_user, UserAuditLog.Action.ACTIVATED)

    return Response(UserListSerializer(target_user).data)


@api_view(["POST"])
@permission_classes([IsAuthenticated, IsAdminOrSuperUser])
def deactivate_user(request, user_id):
    """POST /api/settings/users/<id>/deactivate/"""

    target_user = _get_target_user(user_id)

    if target_user is None:
        return Response(
            {"detail": "User not found."},
            status=status.HTTP_404_NOT_FOUND,
        )

    if not _can_manage_user(request.user, target_user):
        return Response(
            {"detail": "You do not have permission to manage this user."},
            status=status.HTTP_403_FORBIDDEN,
        )

    if target_user.pk == request.user.pk:
        return Response(
            {"detail": "You cannot deactivate your own account."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    if UserProfile.is_last_active_system_owner(target_user):
        return Response(
            {"detail": "Cannot deactivate the last active System Owner."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    target_user.is_active = False
    target_user.save(update_fields=["is_active"])

    _log(request.user, target_user, UserAuditLog.Action.DEACTIVATED)

    return Response(UserListSerializer(target_user).data)


# ==================================================================
# ADMIN-INITIATED PASSWORD RESET
# ==================================================================


@api_view(["POST"])
@permission_classes([IsAuthenticated, IsAdminOrSuperUser])
def reset_user_password(request, user_id):
    """
    POST /api/settings/users/<id>/reset-password/

    Lets a System Owner/Super User/Admin set a new password for
    another account, within their manageable role scope.
    """

    target_user = _get_target_user(user_id)

    if target_user is None:
        return Response(
            {"detail": "User not found."},
            status=status.HTTP_404_NOT_FOUND,
        )

    if not _can_manage_user(request.user, target_user):
        return Response(
            {"detail": "You do not have permission to reset this user's password."},
            status=status.HTTP_403_FORBIDDEN,
        )

    serializer = AdminPasswordResetSerializer(
        data=request.data,
        context={"target_user": target_user},
    )

    if not serializer.is_valid():
        return Response(
            {"detail": serializer.errors},
            status=status.HTTP_400_BAD_REQUEST,
        )

    with transaction.atomic():
        new_password = str(serializer.validated_data.get("new_password") or "")
        target_user.set_password(new_password)
        target_user.save(update_fields=["password"])

    return Response({"message": "Password reset successfully."})


# ==================================================================
# FAMILIES  (System Owner only - see users.permissions matrix)
# ==================================================================


def _get_group(group_id):
    try:
        return FamilyGroup.objects.get(pk=group_id)
    except FamilyGroup.DoesNotExist:
        return None


@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated, IsSystemOwner])
def group_list(request):
    """
    GET  /api/settings/groups/  - list every family with members
                                    (System Owner only - "View all
                                    families").
    POST /api/settings/groups/  - create a new family (System
                                    Owner only - "Add family").
    """

    if request.method == "GET":
        groups = FamilyGroup.objects.select_related("created_by").order_by("name")

        return Response(FamilyGroupSerializer(groups, many=True).data)

    serializer = FamilyGroupCreateSerializer(data=request.data, context={"request": request})

    if not serializer.is_valid():
        return Response({"detail": serializer.errors}, status=status.HTTP_400_BAD_REQUEST)

    group = serializer.save()

    return Response(FamilyGroupSerializer(group).data, status=status.HTTP_201_CREATED)


@api_view(["PATCH", "DELETE"])
@permission_classes([IsAuthenticated, IsSystemOwner])
def group_detail(request, group_id):
    """
    PATCH  /api/settings/groups/<id>/  - rename a family (System
                                          Owner only).
    DELETE /api/settings/groups/<id>/  - delete a family (members
                                          simply lose the shared-
                                          visibility grant; their
                                          own accounts/data are
                                          untouched). System Owner
                                          only.
    """

    group = _get_group(group_id)

    if group is None:
        return Response({"detail": "Family not found."}, status=status.HTTP_404_NOT_FOUND)

    if request.method == "DELETE":
        group.delete()

        return Response({"message": "Family deleted successfully."})

    serializer = FamilyGroupUpdateSerializer(group, data=request.data, partial=True)

    if not serializer.is_valid():
        return Response({"detail": serializer.errors}, status=status.HTTP_400_BAD_REQUEST)

    group = serializer.save()

    return Response(FamilyGroupSerializer(group).data)


@api_view(["POST"])
@permission_classes([IsAuthenticated, IsSystemOwner])
def group_add_member(request, group_id):
    """
    POST /api/settings/groups/<id>/members/

    Body: {"user_id": <id>}

    Adds a user to this family, IN ADDITION to any other families
    they already belong to (multi-family assignment is fully
    supported - membership no longer moves a user out of any
    other family). System Owner only.
    """

    group = _get_group(group_id)

    if group is None:
        return Response({"detail": "Family not found."}, status=status.HTTP_404_NOT_FOUND)

    user_id = request.data.get("user_id")

    target_user = _get_target_user(user_id) if user_id is not None else None

    if target_user is None:
        return Response({"detail": "User not found."}, status=status.HTTP_404_NOT_FOUND)

    profile = getattr(target_user, "profile", None)

    if profile is None:
        return Response(
            {"detail": "User profile not found."},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
    profile.family_groups.add(group)

    _log(request.user, target_user, UserAuditLog.Action.FAMILY_ADDED, new_value=group.name)

    return Response(FamilyGroupSerializer(group).data)


@api_view(["DELETE"])
@permission_classes([IsAuthenticated, IsSystemOwner])
def group_remove_member(request, group_id, user_id):
    """DELETE /api/settings/groups/<id>/members/<user_id>/

    System Owner only. Removes just this one family membership -
    any other families the user belongs to are untouched.
    """

    group = _get_group(group_id)

    if group is None:
        return Response({"detail": "Family not found."}, status=status.HTTP_404_NOT_FOUND)

    target_user = _get_target_user(user_id)

    if target_user is None:
        return Response({"detail": "User not found."}, status=status.HTTP_404_NOT_FOUND)

    profile = getattr(target_user, "profile", None)

    if profile is None:
        return Response(
            {"detail": "User profile not found."},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    if not profile.family_groups.filter(pk=group.pk).exists():
        return Response(
            {"detail": "This user is not a member of this family."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    profile.family_groups.remove(group)

    # If the family removed was the user's active one, clear it so
    # the next read recomputes a valid default.
    if profile.active_family_group_id == group.pk:
        profile.active_family_group = None
        profile.save(update_fields=["active_family_group", "updated_at"])

    _log(request.user, target_user, UserAuditLog.Action.FAMILY_REMOVED, old_value=group.name)

    return Response(FamilyGroupSerializer(group).data)
