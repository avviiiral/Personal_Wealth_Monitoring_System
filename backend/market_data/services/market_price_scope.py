"""Shared scope for assets eligible for automated market-price refreshes.

Legacy Excel imports may have stored an actual listed Direct Equity security
with Asset.category=OTHER. The linked transaction hierarchy is authoritative
for that narrow compatibility case; unrelated OTHER assets are not sent to
market-data providers.

Mutual funds are never part of the exchange-price universe. A fund is any of:

* an asset with category MUTUAL_FUND;
* an asset with a transaction whose sub-class contains "mutual fund";
* an ISIN starting with INF that is not an ETF (category ETF, or " ETF" /
  "BEES" in the name).

Fund NAVs are refreshed by the nightly AMFI job instead.
"""
from django.db.models import Exists, F, OuterRef, Q, Subquery

from config.pwms_config import get as get_pwms_config
from investments.models import Asset, AssetCategory, Transaction

DEFAULT_NO_PRICE_SUB_CLASS_KEYWORDS = (
    "AIF",
    "ALTERNATIVE INVESTMENT",
    "UNLISTED",
    "PRIVATE EQUITY",
    "PRE-IPO",
    "PRE IPO",
)


def _etf_q(prefix=""):
    return (
        Q(**{f"{prefix}category": AssetCategory.ETF})
        | Q(**{f"{prefix}name__icontains": " ETF"})
        | Q(**{f"{prefix}name__icontains": "BEES"})
    )


def _is_etf(asset):
    name = f" {(getattr(asset, 'name', '') or '').upper()}"
    return getattr(asset, "category", None) == AssetCategory.ETF or " ETF" in name or "BEES" in name


def mutual_fund_q():
    """Queryset filter matching every fund (see module docstring)."""
    fund_transaction = Transaction.objects.filter(asset_id=OuterRef("pk"), sub_class__icontains="mutual fund")
    return (
        Q(category=AssetCategory.MUTUAL_FUND)
        | Q(Exists(fund_transaction))
        | (Q(isin__istartswith="INF") & ~_etf_q())
    )


def is_mutual_fund_asset(asset):
    if getattr(asset, "category", None) == AssetCategory.MUTUAL_FUND:
        return True
    asset_id = getattr(asset, "pk", None) or getattr(asset, "id", None)
    if asset_id is not None and Transaction.objects.filter(
        asset_id=asset_id, sub_class__icontains="mutual fund"
    ).exists():
        return True
    isin = (getattr(asset, "isin", "") or "").strip().upper()
    return isin.startswith("INF") and not _is_etf(asset)


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
    """Return the shared refresh universe, including legacy Direct Equity.

    ``include_mutual_funds=False`` excludes every fund, including funds whose
    category was mislabelled BOND/STOCK by an older import.
    """
    categories = ["STOCK", "ETF", "BOND"]
    if include_mutual_funds:
        categories.append("MUTUAL_FUND")

    legacy_direct_equity = Q(
        category="OTHER",
        transactions__asset_class__iexact="Equity",
        transactions__sub_class__iexact="Direct Equity",
    )
    queryset = (
        Asset.objects.filter(
            Q(category__in=categories)
            | Q(name__icontains="SOVEREIGN GOLD BOND")
            | Q(name__icontains=" ETF")
            | legacy_direct_equity
        )
        .distinct()
    )
    if not include_mutual_funds:
        queryset = queryset.exclude(mutual_fund_q())
    return queryset


def no_price_sub_class_keywords():
    configured = (get_pwms_config("scheduler", "jobs", {}) or {}).get("market_prices", {})
    keywords = configured.get("excluded_sub_class_keywords", DEFAULT_NO_PRICE_SUB_CLASS_KEYWORDS)
    return [str(keyword).strip() for keyword in keywords if str(keyword).strip()]


def _no_market_price_q():
    """Assets that have no exchange price: AIFs, unlisted shares, ISIN-less bonds."""
    keyword_q = Q()
    for keyword in no_price_sub_class_keywords():
        keyword_q |= Q(sub_class__icontains=keyword)
    condition = Q(category=AssetCategory.BOND, isin__isnull=True) | Q(category=AssetCategory.BOND, isin="")
    if keyword_q:
        no_price_transaction = Transaction.objects.filter(asset_id=OuterRef("pk")).filter(keyword_q)
        condition |= Q(Exists(no_price_transaction))
    return condition


def price_loop_assets():
    """Assets for the 15-minute job, stalest first.

    Annotates ``latest_price_date`` and ``latest_fetched_at`` from the newest
    exchange-sourced MarketPrice row (Yahoo for stocks/ETFs, OTHER for SGBs
    and bonds). Order: never fetched / unknown fetch time first, then the
    oldest fetch; ties go to the oldest price date. Ordering by fetch time
    rather than price date means a suspended stock (old date, fetched every
    cycle) cannot permanently jump the queue.
    """
    from market_data.models import DataSource, MarketPrice

    latest_row = MarketPrice.objects.filter(
        asset_id=OuterRef("pk"),
        source__in=[DataSource.YAHOO_FINANCE, DataSource.OTHER],
    ).order_by("-date", "-id")
    return (
        market_price_assets(include_mutual_funds=False)
        .exclude(_no_market_price_q())
        .annotate(
            latest_price_date=Subquery(latest_row.values("date")[:1]),
            latest_fetched_at=Subquery(latest_row.values("fetched_at")[:1]),
        )
        .order_by(
            F("latest_fetched_at").asc(nulls_first=True),
            F("latest_price_date").asc(nulls_first=True),
            "name",
            "id",
        )
    )
