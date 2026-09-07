from django.contrib import admin

from .models import (
    FamilyGroup,
    FamilyMembership,
    UserAuditLog,
    UserPreference,
    UserProfile,
)


@admin.register(UserPreference)
class UserPreferenceAdmin(admin.ModelAdmin):

    list_display = (
        "user",
        "currency",
        "date_format",
        "default_analytics_period",
        "updated_at",
    )

    list_filter = (
        "currency",
        "date_format",
        "default_analytics_period",
    )

    search_fields = (
        "user__username",
        "user__email",
    )

    readonly_fields = (
        "created_at",
        "updated_at",
    )


class FamilyMembershipInline(admin.TabularInline):
    model = FamilyMembership
    fk_name = "profile"
    extra = 0
    readonly_fields = ("added_by", "created_at")
    autocomplete_fields = ("family_group",)


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):

    list_display = (
        "user",
        "role",
        "active_family_group",
        "created_by",
        "updated_at",
    )

    list_filter = (
        "role",
    )

    search_fields = (
        "user__username",
        "user__email",
    )

    readonly_fields = (
        "created_at",
        "updated_at",
    )

    inlines = [FamilyMembershipInline]


@admin.register(FamilyGroup)
class FamilyGroupAdmin(admin.ModelAdmin):

    list_display = (
        "name",
        "created_by",
        "created_at",
    )

    search_fields = (
        "name",
    )

    readonly_fields = (
        "created_at",
    )


@admin.register(FamilyMembership)
class FamilyMembershipAdmin(admin.ModelAdmin):

    list_display = (
        "profile",
        "family_group",
        "added_by",
        "created_at",
    )

    list_filter = (
        "family_group",
    )

    search_fields = (
        "profile__user__username",
        "family_group__name",
    )

    readonly_fields = (
        "created_at",
    )


@admin.register(UserAuditLog)
class UserAuditLogAdmin(admin.ModelAdmin):
    """Read-only: the audit trail must never be edited from Django
    admin, only viewed."""

    list_display = (
        "created_at",
        "actor",
        "action",
        "target_username",
        "old_value",
        "new_value",
    )

    list_filter = (
        "action",
    )

    search_fields = (
        "target_username",
        "actor__username",
    )

    readonly_fields = (
        "actor",
        "target_user_id",
        "target_username",
        "action",
        "old_value",
        "new_value",
        "created_at",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
