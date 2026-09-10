"""
Test-only compatibility helpers for the Portfolio News suite.

The production application now requires an explicit active FamilyGroup for
family-scoped financial data. The older Portfolio News tests create users and
owner-based records without creating family fixtures. This module bridges that
legacy test setup into the new ownership model without changing production
family resolution or authorization behavior.

It is imported only by backend/manage.py when the Django test command is run.
"""

from functools import wraps

from django.contrib.auth.models import User
from django.db import models

from users.models import FamilyGroup, FamilyMembership


_INSTALLED = False


def _ensure_test_family(user):
    """Give a test user one explicit family and select it as active."""
    profile = user.profile

    family = FamilyGroup.objects.filter(
        created_by=user,
        name=f"Test Family - {user.username}",
    ).first()

    if family is None:
        family = FamilyGroup.objects.create(
            name=f"Test Family - {user.username}",
            created_by=user,
        )

    FamilyMembership.objects.get_or_create(
        profile=profile,
        family_group=family,
        defaults={"added_by": user},
    )

    if profile.active_family_group_id != family.id:
        profile.active_family_group = family
        profile.save(update_fields=["active_family_group", "updated_at"])

    return family


def install():
    """Install test-only compatibility wrappers once per Python process."""
    global _INSTALLED

    if _INSTALLED:
        return

    _INSTALLED = True

    original_create_user = User._default_manager.create_user

    @wraps(original_create_user)
    def create_user_with_test_family(manager, *args, **kwargs):
        user = original_create_user(*args, **kwargs)
        _ensure_test_family(user)
        return user

    User._default_manager.create_user = create_user_with_test_family.__get__(
        User._default_manager,
        type(User._default_manager),
    )

    family_owned_models = {
        "investments.Asset",
        "investments.Holding",
        "mutual_funds.MutualFundScheme",
        "mutual_funds.MutualFundHolding",
        "portfolio_news.PortfolioNewsAlert",
    }

    original_manager_create = models.Manager.create

    @wraps(original_manager_create)
    def create_with_test_family(manager, *args, **kwargs):
        model = getattr(manager, "model", None)
        label = model._meta.label if model is not None else None

        if label in family_owned_models and "family_group" not in kwargs:
            owner = kwargs.get("owner")
            user = kwargs.get("user")

            if owner is not None:
                kwargs["family_group"] = _ensure_test_family(owner)
            elif user is not None:
                kwargs["family_group"] = _ensure_test_family(user)

        return original_manager_create(manager, *args, **kwargs)

    models.Manager.create = create_with_test_family
