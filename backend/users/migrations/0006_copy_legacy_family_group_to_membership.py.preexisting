from django.db import migrations


def copy_family_group_forward(apps, schema_editor):
    """
    Preserve every existing user's single family assignment by
    creating an equivalent FamilyMembership row before the old
    `family_group` FK field is removed (migration 0007). No
    existing family relationship is lost - it becomes the user's
    first (and, at this point, only) entry in the new many-to-many
    family_groups relation, and also their initial active family
    (so their scoped views are unchanged immediately after this
    migration runs).
    """

    UserProfile = apps.get_model("users", "UserProfile")
    FamilyMembership = apps.get_model("users", "FamilyMembership")

    profiles = UserProfile.objects.exclude(family_group_id__isnull=True)

    for profile in profiles:
        FamilyMembership.objects.get_or_create(
            profile_id=profile.id,
            family_group_id=profile.family_group_id,
        )

        profile.active_family_group_id = profile.family_group_id
        profile.save(update_fields=["active_family_group"])


def copy_family_group_backward(apps, schema_editor):
    """
    Reverse: collapse each profile's family memberships back onto
    the single legacy `family_group` FK (best-effort - if a
    profile had been given more than one family in the meantime,
    only one is kept, since the old schema cannot represent more).
    """

    UserProfile = apps.get_model("users", "UserProfile")

    for profile in UserProfile.objects.all():
        membership = profile.family_memberships.order_by("family_group_id").first()

        profile.family_group_id = membership.family_group_id if membership else None
        profile.save(update_fields=["family_group"])


class Migration(migrations.Migration):

    dependencies = [
        ("users", "0005_add_family_membership_and_audit_models"),
    ]

    operations = [
        migrations.RunPython(copy_family_group_forward, copy_family_group_backward),
    ]
