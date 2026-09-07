from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt, ensure_csrf_cookie

from rest_framework.decorators import (
    api_view,
    authentication_classes,
    permission_classes,
)
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response

from users.models import UserPreference
from users.serializers import CurrentUserSerializer


# ==========================================================
# HEALTH / CSRF
# ==========================================================

@ensure_csrf_cookie
def health_check(request):
    """
    Basic PWMS backend health-check endpoint.

    Also ensures that Django sends the CSRF cookie.
    """
    return JsonResponse({
        "status": "success",
        "service": "Personal Wealth Monitoring System",
        "backend": "Django",
        "message": "PWMS backend is running",
        "authenticated": request.user.is_authenticated,
        "user": (
            request.user.username
            if request.user.is_authenticated
            else None
        ),
    })


# ==========================================================
# AUTHENTICATION
# ==========================================================

@csrf_exempt
@api_view(["POST"])
@authentication_classes([])
@permission_classes([AllowAny])
def login_view(request):
    """
    Authenticate a PWMS user using Django session authentication.

    Login does not depend on an existing authenticated session.
    This prevents a stale/previous session from causing DRF
    SessionAuthentication to reject the login request with 403.
    """

    username = request.data.get("username")
    password = request.data.get("password")

    if not username or not password:
        return Response(
            {
                "detail": (
                    "Username and password are required."
                )
            },
            status=400,
        )

    user = authenticate(
        request=request,
        username=username,
        password=password,
    )

    if user is None:
        return Response(
            {
                "detail": (
                    "Invalid username or password."
                )
            },
            status=401,
        )

    login(request, user)

    return Response({
        "authenticated": True,
        "user": CurrentUserSerializer(user).data,
    })


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def logout_view(request):
    """
    End the current Django session.
    """

    logout(request)

    return Response({
        "authenticated": False,
        "message": "Logged out successfully.",
    })


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def current_user(request):
    """
    GET /api/auth/me/

    Return the currently authenticated PWMS user, including their
    role, derived permission flags, family memberships, and
    currently active family - everything the frontend needs to
    render a role-appropriate UI. Backend authorization is always
    re-checked independently on every subsequent request; this
    payload is for display/navigation only.
    """

    return Response({
        "authenticated": True,
        "user": CurrentUserSerializer(request.user).data,
    })


# ==========================================================
# SETTINGS
# ==========================================================

def _get_or_create_preferences(user):
    """
    Return the user's preferences.

    Preferences are created automatically the first time
    the Settings API is accessed.
    """

    preferences, _ = UserPreference.objects.get_or_create(
        user=user,
    )

    return preferences


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def settings_view(request):
    """
    Return the authenticated user's settings.
    """

    user = request.user

    preferences = _get_or_create_preferences(user)

    return Response({
        "profile": {
            "id": user.id,
            "username": user.username,
            "email": user.email,
        },
        "preferences": {
            "currency": preferences.currency,
            "date_format": preferences.date_format,
            "default_analytics_period": (
                preferences.default_analytics_period
            ),
        },
    })


@api_view(["PATCH"])
@permission_classes([IsAuthenticated])
def update_settings(request):
    """
    Update the authenticated user's profile/preferences.
    """

    user = request.user

    preferences = _get_or_create_preferences(user)

    # ------------------------------------------------------
    # EMAIL
    # ------------------------------------------------------

    if "email" in request.data:

        email = request.data.get("email")

        if email is None:
            return Response(
                {
                    "detail": "Email cannot be null."
                },
                status=400,
            )

        email = str(email).strip()

        if len(email) > 254:
            return Response(
                {
                    "detail": "Email address is too long."
                },
                status=400,
            )

        user.email = email

    # ------------------------------------------------------
    # CURRENCY
    # ------------------------------------------------------

    if "currency" in request.data:

        currency = request.data.get("currency")

        valid_currencies = {
            choice[0]
            for choice in UserPreference.CURRENCY_CHOICES
        }

        if currency not in valid_currencies:
            return Response(
                {
                    "detail": "Invalid currency."
                },
                status=400,
            )

        preferences.currency = currency

    # ------------------------------------------------------
    # DATE FORMAT
    # ------------------------------------------------------

    if "date_format" in request.data:

        date_format = request.data.get(
            "date_format"
        )

        valid_formats = {
            choice[0]
            for choice in UserPreference.DATE_FORMAT_CHOICES
        }

        if date_format not in valid_formats:
            return Response(
                {
                    "detail": "Invalid date format."
                },
                status=400,
            )

        preferences.date_format = date_format

    # ------------------------------------------------------
    # ANALYTICS PERIOD
    # ------------------------------------------------------

    if "default_analytics_period" in request.data:

        try:
            period = int(
                request.data.get(
                    "default_analytics_period"
                )
            )

        except (
            TypeError,
            ValueError,
        ):
            return Response(
                {
                    "detail": (
                        "Analytics period must be "
                        "a valid number."
                    )
                },
                status=400,
            )

        valid_periods = {
            choice[0]
            for choice in (
                UserPreference
                .ANALYTICS_PERIOD_CHOICES
            )
        }

        if period not in valid_periods:
            return Response(
                {
                    "detail": (
                        "Invalid analytics period."
                    )
                },
                status=400,
            )

        preferences.default_analytics_period = period

    user.save(
        update_fields=[
            "email",
        ]
    )

    preferences.save()

    return Response({
        "message": "Settings updated successfully.",
        "profile": {
            "id": user.id,
            "username": user.username,
            "email": user.email,
        },
        "preferences": {
            "currency": preferences.currency,
            "date_format": preferences.date_format,
            "default_analytics_period": (
                preferences.default_analytics_period
            ),
        },
    })


# ==========================================================
# CHANGE PASSWORD
# ==========================================================

@api_view(["POST"])
@permission_classes([IsAuthenticated])
def change_password(request):
    """
    Change the authenticated user's password.
    """

    current_password = request.data.get(
        "current_password"
    )

    new_password = request.data.get(
        "new_password"
    )

    confirm_password = request.data.get(
        "confirm_password"
    )

    if not current_password:
        return Response(
            {
                "detail": (
                    "Current password is required."
                )
            },
            status=400,
        )

    if not new_password:
        return Response(
            {
                "detail": (
                    "New password is required."
                )
            },
            status=400,
        )

    if new_password != confirm_password:
        return Response(
            {
                "detail": (
                    "New passwords do not match."
                )
            },
            status=400,
        )

    if not request.user.check_password(
        current_password
    ):
        return Response(
            {
                "detail": (
                    "Current password is incorrect."
                )
            },
            status=400,
        )

    try:
        validate_password(
            new_password,
            request.user,
        )

    except ValidationError as exc:
        return Response(
            {
                "detail": exc.messages,
            },
            status=400,
        )

    request.user.set_password(
        new_password
    )

    request.user.save(
        update_fields=[
            "password",
        ]
    )

    # Re-authenticate the session after changing
    # the password so the current user is not
    # unexpectedly logged out.
    from django.contrib.auth import update_session_auth_hash

    update_session_auth_hash(
        request,
        request.user,
    )

    return Response({
        "message": (
            "Password changed successfully."
        ),
    })