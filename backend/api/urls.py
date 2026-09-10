from django.urls import path

from .views import (
    health_check,
    login_view,
    logout_view,
    current_user,
    settings_view,
    update_settings,
    change_password,
)


urlpatterns = [

    # ------------------------------------------------------
    # HEALTH
    # ------------------------------------------------------

    path(
        "health/",
        health_check,
        name="health-check",
    ),

    # ------------------------------------------------------
    # AUTHENTICATION
    # ------------------------------------------------------

    path(
        "auth/login/",
        login_view,
        name="auth-login",
    ),

    path(
        "auth/logout/",
        logout_view,
        name="auth-logout",
    ),

    path(
        "auth/me/",
        current_user,
        name="auth-me",
    ),

    # ------------------------------------------------------
    # SETTINGS
    # ------------------------------------------------------

    path(
        "settings/",
        settings_view,
        name="settings",
    ),

    path(
        "settings/update/",
        update_settings,
        name="settings-update",
    ),

    path(
        "settings/change-password/",
        change_password,
        name="change-password",
    ),
]