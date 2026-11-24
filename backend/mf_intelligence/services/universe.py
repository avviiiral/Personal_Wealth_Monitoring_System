"""Which schemes make up the peer group of a category."""

import re

from django.db.models import Q

from mf_intelligence.constants import GROWTH_OPTIONS, PLAN_CHOICES, PLAN_OTHER, PLAN_REGULAR, SINGLE_PLAN_OPTIONS
from mf_intelligence.models import MFScheme


# Special-purpose plans that are not investable peers of the main fund.
EXCLUDED_NAME = re.compile(r"unclaimed|segregat|discontinued", re.IGNORECASE)


def normalise_plan(value):
    text = (value or "").strip().lower()
    for plan in PLAN_CHOICES:
        if text == plan.lower():
            return plan
    return PLAN_REGULAR


def is_single_plan_category(category):
    return (category.asset_class or "").upper() == "ETF"


def peer_filter(category, plan):
    """Q object selecting the growth-option schemes of ``plan``."""
    if is_single_plan_category(category):
        return Q(plan=PLAN_OTHER, option__in=SINGLE_PLAN_OPTIONS)
    return Q(plan=plan, option__in=GROWTH_OPTIONS)


def peer_schemes(category, plan):
    queryset = MFScheme.objects.filter(category=category, is_active=True).filter(peer_filter(category, plan))
    return [scheme for scheme in queryset if not EXCLUDED_NAME.search(scheme.name)]


def sync_target_schemes(category, plans):
    """Schemes whose NAV / holdings / AUM the sync fetches for ``category``."""
    if is_single_plan_category(category):
        condition = Q(plan=PLAN_OTHER, option__in=SINGLE_PLAN_OPTIONS)
    else:
        condition = Q(plan__in=list(plans), option__in=GROWTH_OPTIONS)
    queryset = MFScheme.objects.filter(category=category, is_active=True).filter(condition).order_by("amfi_code")
    return [scheme for scheme in queryset if not EXCLUDED_NAME.search(scheme.name)]
