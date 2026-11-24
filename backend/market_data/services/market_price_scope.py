"""Shared scope for assets eligible for automated market-price refreshes.

Legacy Excel imports may have stored an actual listed Direct Equity security
with Asset.category=OTHER. The linked transaction hierarchy is authoritative
for that narrow compatibility case; unrelated OTHER assets are not sent to
market-data providers.
"""
from django.db.models import Q

from investments.models import Asset, Transaction


def is_legacy_direct_equity_asset(asset):
    """Return True only for OTHER assets explicitly held as Direct Equity."""
    asset_id = getattr(asset, "pk", None) or getattr(asset, "id", None)
    if asset_id is None or getattr(asset, "category", None) != "OTHER":
        return False
    return Transaction.objects.filter(
        asset_id=asset_id,
        asset_class__iexact="Equity",
        sub_class__iexact="Direct Equity",
    ).exists()


def market_price_assets(*, include_mutual_funds):
    """Return the shared refresh universe, including legacy Direct Equity."""
    categories = ["STOCK", "ETF", "BOND"]
    if include_mutual_funds:
        categories.append("MUTUAL_FUND")

    legacy_direct_equity = Q(
        category="OTHER",
        transactions__asset_class__iexact="Equity",
        transactions__sub_class__iexact="Direct Equity",
    )
    return (
        Asset.objects.filter(
            Q(category__in=categories)
            | Q(name__icontains="SOVEREIGN GOLD BOND")
            | Q(name__icontains=" ETF")
            | legacy_direct_equity
        )
        .distinct()
    )
