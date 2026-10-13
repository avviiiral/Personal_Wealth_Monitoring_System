from decimal import Decimal, InvalidOperation

from django.db import IntegrityError
from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Q
from django.db.models.deletion import ProtectedError

from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from investments.models import Asset, Transaction

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


def _portfolio_asset_name(family, asset):
    """Return the canonical displayed name from this family's transactions."""
    name = (
        Transaction.objects
        .filter(family=family, asset=asset, asset_name__isnull=False)
        .exclude(asset_name="")
        .order_by("id")
        .values_list("asset_name", flat=True)
        .first()
    )
    if name:
        return name

    # Duplicate Asset records may represent the same underlying security.
    return (
        Transaction.objects
        .filter(
            family=family,
            asset__name=asset.name,
            asset_name__isnull=False,
        )
        .exclude(asset_name="")
        .order_by("id")
        .values_list("asset_name", flat=True)
        .first()
        or asset.name
    )


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


def _log_tax_change(user, family, asset, before, after, asset_name=None):
    TaxRateChangeLog.objects.create(
        user=user,
        username=user.username,
        family=family,
        family_name=family.name,
        asset=asset,
        asset_name=asset_name or _portfolio_asset_name(family, asset),
        change_from=_tax_values(before),
        change_to=_tax_values(after),
    )


def _global_tax_policy_queryset():
    return TaxRateSetting.objects.filter(
        family__isnull=True,
        asset__isnull=True,
    )


def _tax_setting_response(row, asset, family, asset_name, configuration_conflict=False):
    return {
        "id": row.id if row else None,
        "asset_id": asset.id if asset else None,
        "asset_name": asset_name,
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
        "configuration_conflict": configuration_conflict,
    }


@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated])
def tax_rate_list(request):
    """GET/POST /api/settings/tax-rates/ for the active family.

    Settings written here are global policies keyed by the canonical
    Portfolio Asset Name. Legacy family/asset rows are retained separately
    within the same table as underlying-security fallback settings.
    """

    family = require_active_family(request.user)

    if request.method == "GET":
        transactions = (
            Transaction.objects
            .filter(family=family, asset_name__isnull=False)
            .exclude(asset_name="")
            .order_by("asset_name", "id")
            .values("asset_name", "asset_id")
        )

        # The Portfolio Asset Name, not the underlying security label, is the
        # canonical global key. Keep one representative asset_id for the UI.
        logical_assets = {}
        for item in transactions:
            logical_assets.setdefault(item["asset_name"], item["asset_id"])

        asset_names = list(logical_assets)
        global_rows = (
            _global_tax_policy_queryset()
            .filter(asset_name__in=asset_names)
            .order_by("-updated_at", "-id")
        )
        settings_by_name = {}
        for row in global_rows:
            settings_by_name.setdefault(row.asset_name, row)

        conflict_names = set(
            TaxRateSetting.objects
            .filter(
                family__isnull=False,
                asset__isnull=False,
                needs_review=True,
                asset_name__in=asset_names,
            )
            .values_list("asset_name", flat=True)
            .distinct()
        )

        results = []
        for asset_name, asset_id in logical_assets.items():
            asset = Asset.objects.filter(pk=asset_id, family=family).first()
            results.append(
                _tax_setting_response(
                    settings_by_name.get(asset_name),
                    asset,
                    family,
                    asset_name,
                    configuration_conflict=asset_name in conflict_names,
                )
            )

        results.sort(key=lambda item: item["asset_name"].casefold())
        return Response(results)

    # POST: the submitted asset must resolve to a displayed name actually
    # represented in the active family's transaction data.
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

    submitted_asset_name = str(request.data.get("asset_name") or "").strip()
    transactions_for_name = Transaction.objects.filter(
        family=family,
        asset_name__isnull=False,
    ).exclude(asset_name="")

    if submitted_asset_name:
        # The display-name key is accepted only when this family actually has
        # a transaction connecting it to the selected Asset/security.
        asset_transaction = (
            transactions_for_name
            .filter(asset=asset, asset_name=submitted_asset_name)
            .select_related("asset")
            .order_by("id")
            .first()
        )
        if asset_transaction is None:
            asset_transaction = (
                transactions_for_name
                .filter(asset__name=asset.name, asset_name=submitted_asset_name)
                .select_related("asset")
                .order_by("id")
                .first()
            )
    else:
        # Backward compatibility for API clients that do not yet send
        # asset_name: use the first canonical display label linked to this
        # exact Asset, then fall back to duplicate internal Asset records.
        asset_transaction = (
            transactions_for_name
            .filter(asset=asset)
            .select_related("asset")
            .order_by("id")
            .first()
        )
        if asset_transaction is None:
            asset_transaction = (
                transactions_for_name
                .filter(asset__name=asset.name)
                .select_related("asset")
                .order_by("id")
                .first()
            )
    if asset_transaction is None:
        return Response(
            {"detail": "Asset is not available in the active family's portfolio."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    asset_name = asset_transaction.asset_name
    # Return the representative ID used by GET so the existing frontend can
    # reconcile the saved row even when POST was submitted for a duplicate Asset.
    canonical_asset = asset_transaction.asset

    tenure_months = request.data.get("tenure_months")
    if tenure_months in ("", None):
        return Response(
            {"detail": "Tenure is required and must be in months."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    try:
        tenure_months = int(tenure_months)
    except (TypeError, ValueError):
        return Response(
            {"detail": "Tenure must be a whole number of months."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    if tenure_months < 0:
        return Response(
            {"detail": "Tenure cannot be negative."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    def parse_tax_rate(field_name):
        value = request.data.get(field_name)
        if value in ("", None):
            raise ValueError(f"{field_name} is required.")
        try:
            parsed = Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError):
            raise ValueError(f"{field_name} must be a valid number.")
        if not parsed.is_finite() or parsed < 0 or parsed > 100:
            raise ValueError(f"{field_name} must be between 0 and 100 percent.")
        return parsed

    try:
        short_term_tax_rate = parse_tax_rate("short_term_tax_rate")
        long_term_tax_rate = parse_tax_rate("long_term_tax_rate")
    except ValueError as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

    with transaction.atomic():
        row = (
            _global_tax_policy_queryset()
            .select_for_update()
            .filter(asset_name=asset_name)
            .first()
        )
        before = (
            TaxRateSetting.objects.get(pk=row.pk)
            if row is not None
            else None
        )
        created = row is None
        if created:
            row = TaxRateSetting.objects.create(
                family=None,
                asset=None,
                asset_name=asset_name,
                tenure_months=tenure_months,
                short_term_tax_rate=short_term_tax_rate,
                long_term_tax_rate=long_term_tax_rate,
            )
        else:
            row.tenure_months = tenure_months
            row.short_term_tax_rate = short_term_tax_rate
            row.long_term_tax_rate = long_term_tax_rate
            row.save(update_fields=[
                "tenure_months",
                "short_term_tax_rate",
                "long_term_tax_rate",
                "updated_at",
            ])

        _log_tax_change(request.user, family, canonical_asset, before, row, asset_name=asset_name)

        # A submitted global policy is the explicit manual resolution for any
        # retained conflicts. Legacy rate values themselves are not overwritten.
        TaxRateSetting.objects.filter(
            family__isnull=False,
            asset__isnull=False,
            asset_name=asset_name,
            needs_review=True,
        ).update(needs_review=False, review_reason="")

    has_legacy_conflict = TaxRateSetting.objects.filter(
        family__isnull=False,
        asset__isnull=False,
        asset_name=asset_name,
        needs_review=True,
    ).exists()
    return Response(
        _tax_setting_response(
            row,
            canonical_asset,
            family,
            asset_name,
            configuration_conflict=has_legacy_conflict,
        ),
        status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
    )


@api_view(["PATCH", "DELETE"])
@permission_classes([IsAuthenticated])
def tax_rate_detail(request, tax_rate_id):
    """PATCH/DELETE a global tax policy visible in the active family."""

    family = require_active_family(request.user)
    visible_names = set(
        Transaction.objects
        .filter(family=family, asset_name__isnull=False)
        .exclude(asset_name="")
        .values_list("asset_name", flat=True)
        .distinct()
    )
    row = (
        _global_tax_policy_queryset()
        .filter(pk=tax_rate_id, asset_name__in=visible_names)
        .first()
    )
    if row is None:
        return Response(
            {"detail": "Tax setting not found."},
            status=status.HTTP_404_NOT_FOUND,
        )

    representative_transaction = (
        Transaction.objects
        .filter(family=family, asset_name=row.asset_name)
        .select_related("asset")
        .order_by("id")
        .first()
    )
    if representative_transaction is None:
        return Response(
            {"detail": "Tax setting not found."},
            status=status.HTTP_404_NOT_FOUND,
        )
    asset = representative_transaction.asset

    has_legacy_conflict = TaxRateSetting.objects.filter(
        family__isnull=False,
        asset__isnull=False,
        asset_name=row.asset_name,
        needs_review=True,
    ).exists()

    if request.method == "DELETE":
        _log_tax_change(request.user, family, asset, row, None, asset_name=row.asset_name)
        row.delete()
        return Response({"message": "Tax setting cleared successfully."})

    updates = {}
    if "tenure_months" in request.data:
        try:
            tenure_months = int(request.data["tenure_months"])
        except (TypeError, ValueError):
            return Response(
                {"detail": "Tenure must be a whole number of months."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if tenure_months < 0:
            return Response(
                {"detail": "Tenure cannot be negative."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        updates["tenure_months"] = tenure_months

    for field_name in ("short_term_tax_rate", "long_term_tax_rate"):
        if field_name in request.data:
            try:
                value = Decimal(str(request.data[field_name]))
            except (InvalidOperation, TypeError, ValueError):
                return Response(
                    {"detail": f"{field_name} must be a valid number."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if not value.is_finite() or value < 0 or value > 100:
                return Response(
                    {"detail": f"{field_name} must be between 0 and 100 percent."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            updates[field_name] = value

    if not updates:
        return Response(
            {"detail": "No tax settings were supplied."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    with transaction.atomic():
        row = (
            _global_tax_policy_queryset()
            .select_for_update()
            .get(pk=tax_rate_id, asset_name__in=visible_names)
        )
        before = TaxRateSetting.objects.get(pk=row.pk)
        for field_name, value in updates.items():
            setattr(row, field_name, value)
        row.save(update_fields=[*updates.keys(), "updated_at"])
        _log_tax_change(request.user, family, asset, before, row, asset_name=row.asset_name)

    return Response(
        _tax_setting_response(
            row,
            asset,
            family,
            row.asset_name,
            configuration_conflict=has_legacy_conflict,
        )
    )


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def tax_rate_change_history(request):
    """GET the audit history for this family and its visible global policies."""

    family = require_active_family(request.user)
    visible_names = (
        Transaction.objects
        .filter(family=family, asset_name__isnull=False)
        .exclude(asset_name="")
        .values_list("asset_name", flat=True)
        .distinct()
    )
    rows = (
        TaxRateChangeLog.objects
        .filter(Q(family=family) | Q(asset_name__in=visible_names))
        .order_by("-changed_at", "-id")
    )

    return Response([
        {
            "id": row.id,
            "user": row.username,
            "date_time": row.changed_at,
            # Preserve the exact historical label. Never relabel an audit
            # event by joining to a current Asset record.
            "asset_name": row.asset_name,
            "change_from": row.change_from,
            "change_to": row.change_to,
            "family_id": row.family_id,
            "family_name": row.family_name,
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
