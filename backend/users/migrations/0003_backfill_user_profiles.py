from django.db import migrations


def backfill_user_profiles(apps, schema_editor):
    """
    Create a UserProfile for every pre-existing user.

    Role assignment for accounts that existed before RBAC was
    introduced:

        - is_superuser=True  -> SUPERUSER (this preserves whatever
          Django superuser accounts already had full access).
        - is_staff=True      -> ADMIN
        - everyone else      -> VIEWER (the safe, read-only default)

    Existing data (users, portfolios, transactions, prices) is not
    touched - this only adds a profile row per user.
    """

    User = apps.get_model("auth", "User")
    UserProfile = apps.get_model("users", "UserProfile")

    for user in User.objects.all():
        if UserProfile.objects.filter(user=user).exists():
            continue

        if user.is_superuser:
            role = "SUPERUSER"
        elif user.is_staff:
            role = "ADMIN"
        else:
            role = "VIEWER"

        UserProfile.objects.create(user=user, role=role)


def noop_reverse(apps, schema_editor):
    """
    Intentionally a no-op. Reversing this migration should not
    delete UserProfile rows created for real accounts - the
    CreateModel operation in the previous migration already
    handles removing the table itself if the whole app is
    unmigrated.
    """


class Migration(migrations.Migration):

    dependencies = [
        ("users", "0002_userprofile"),
    ]

    operations = [
        migrations.RunPython(backfill_user_profiles, noop_reverse),
    ]
