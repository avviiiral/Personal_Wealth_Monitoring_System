"""Delete PWMS application data while preserving account/authentication data."""
from users.management.commands._reset_base import PhysicalTableResetCommand


class Command(PhysicalTableResetCommand):
    help = (
        "Delete financial, portfolio, market, mutual-fund, watchlist and other "
        "application data while preserving users, roles, profiles, families, "
        "memberships, preferences and Django authorization metadata."
    )
    scope = "account"
    description = "delete all application data EXCEPT account/authentication data"
