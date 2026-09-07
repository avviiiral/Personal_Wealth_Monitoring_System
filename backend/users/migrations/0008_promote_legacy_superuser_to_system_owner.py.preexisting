from django.db import migrations


LEGACY_TOP_ROLE = "SUPERUSER"
NEW_TOP_ROLE = "SYSTEM_OWNER"


def promote_forward(apps, schema_editor):
    """
    Safely re-map existing roles onto the new 4-tier hierarchy.

    Under the previous 3-role model, "SUPERUSER" was the HIGHEST
    role (unrestricted authority, 1:1 with Django's own
    `is_superuser` flag). The new model reuses that same stored
    string for a *different*, second-highest role ("Super User"),
    and introduces SYSTEM_OWNER as the new top role. Every existing
    holder of the old top role therefore needs to become a System
    Owner, or they would be unexpectedly demoted a full level by
    this migration and could even lock the application out of any
    System Owner-only feature (family management, unrestricted user
    administration) if they were the only such account.

    ADMIN and VIEWER need no re-mapping - their meaning is
    unchanged.

    Django's `is_superuser`/`is_staff` flags are left untouched:
    they were already True for every one of these accounts (the
    old signal kept them in sync with the top role), and remain
    correct for SYSTEM_OWNER, the role they now map to.
    """

    UserProfile = apps.get_model("users", "UserProfile")

    UserProfile.objects.filter(role=LEGACY_TOP_ROLE).update(role=NEW_TOP_ROLE)


def promote_backward(apps, schema_editor):
    """
    Reverse: any System Owner becomes (legacy) Super User again.
    Best-effort only - if new-model-only accounts were promoted to
    System Owner after this migration ran forward, this collapses
    them back to the old top role too, which is the closest
    equivalent the 3-role schema can represent.
    """

    UserProfile = apps.get_model("users", "UserProfile")

    UserProfile.objects.filter(role=NEW_TOP_ROLE).update(role=LEGACY_TOP_ROLE)


class Migration(migrations.Migration):

    dependencies = [
        ("users", "0007_remove_legacy_family_group_fk"),
    ]

    operations = [
        migrations.RunPython(promote_forward, promote_backward),
    ]
