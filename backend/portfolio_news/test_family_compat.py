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

    # The legacy digest unit tests call build_daily_digest(user) without the
    # newly required family_group_id. Keep this compatibility strictly inside
    # the test process; production callers still have to pass an explicit
    # family through the normal active-family resolver.
    from portfolio_news.services import digest as digest_service

    original_build_daily_digest = digest_service.build_daily_digest

    @wraps(original_build_daily_digest)
    def build_daily_digest_with_test_family(
        user, family_group_id=None, for_date=None
    ):
        if family_group_id is None:
            family_group_id = _ensure_test_family(user).id
        return original_build_daily_digest(
            user,
            family_group_id=family_group_id,
            for_date=for_date,
        )

    digest_service.build_daily_digest = build_daily_digest_with_test_family

    # The production pipeline now uses the batch analyzer API. A few legacy
    # pipeline tests inject a small fake analyzer that only implements the old
    # analyze() method. Add the batch-shaped adapter only when such a fake is
    # passed, and only in the Django test process.
    from portfolio_news.services import pipeline as pipeline_service

    original_run_monitor = pipeline_service.run_portfolio_news_monitor

    @wraps(original_run_monitor)
    def run_monitor_with_legacy_analyzer(*args, **kwargs):
        analyzer = kwargs.get("analyzer")
        if analyzer is None and len(args) >= 2:
            analyzer = args[1]

        if (
            analyzer is not None
            and not hasattr(analyzer, "analyze_batch")
            and hasattr(analyzer, "analyze")
        ):
            def analyze_batch(items, user=None):
                results = []
                for item in items:
                    if isinstance(item, dict):
                        article = item.get("article")
                        holding = item.get("holding")
                    else:
                        article, holding = item
                    results.append(
                        analyzer.analyze(article, holding, user=user)
                    )
                return results

            analyzer.analyze_batch = analyze_batch

        return original_run_monitor(*args, **kwargs)

    pipeline_service.run_portfolio_news_monitor = run_monitor_with_legacy_analyzer
