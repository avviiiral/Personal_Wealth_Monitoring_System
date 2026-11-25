"""PWMS flush command.

Overrides Django's built-in flush so unmanaged physical tables are included.
The django_migrations table and database schema are never removed.
"""
from users.management.commands._reset_base import PhysicalTableResetCommand


class Command(PhysicalTableResetCommand):
    help = (
        "Delete all application data, including unmanaged tables, while "
        "preserving the database schema and Django migration history."
    )
    scope = "all"
    description = "delete ALL application data (including user and MF data)"
    recreate_post_migrate_data = True
