from django.db.models import Q
from django.contrib.auth.models import User
from investments.models import Asset, AssetCategory, Holding
from watchlist.models import WatchListEntry


# Corporate filings can be meaningful for listed equity securities and ETFs.
# Other asset classes (mutual funds, deposits, bonds, cash, etc.) should not
# be matched merely because a company name happens to appear in the filing.
FILING_MATCHABLE_CATEGORIES = (
    AssetCategory.STOCK,
    AssetCategory.ETF,
)


def _asset_identity_filters(filing):
    """Build conservative identity filters in strongest-first order."""
    filters = []

    if filing.isin:
        filters.append(
            (
                "ISIN",
                Q(isin__iexact=filing.isin)
                | Q(security_master__isin__iexact=filing.isin),
            )
        )

    if filing.symbol:
        filters.append(
            (
                "EXCHANGE_SYMBOL",
                Q(symbol__iexact=filing.symbol),
            )
        )

    if filing.bse_code:
        filters.append(
            (
                "BSE_CODE",
                Q(symbol__iexact=filing.bse_code),
            )
        )

    if filing.company_name:
        filters.append(
            (
                "COMPANY_NAME",
                Q(name__iexact=filing.company_name)
                | Q(security_master__asset_name__iexact=filing.company_name),
            )
        )

    return filters


def match_assets(filing):
    """Match a filing to active portfolio securities by stable identity.

    Matching is deliberately independent of the asset's display section or
    portfolio label. A listed ETF therefore receives the same corporate filing
    treatment as a listed stock when NSE/BSE identifies that security.

    Priority:
      1. ISIN (Asset or SecurityMaster)
      2. exchange symbol
      3. BSE code
      4. exact security/company name
    """
    base = (
        Asset.objects
        .filter(
            is_active=True,
            category__in=FILING_MATCHABLE_CATEGORIES,
        )
        .select_related("security_master")
    )

    for method, condition in _asset_identity_filters(filing):
        assets = list(base.filter(condition).distinct())
        if assets:
            return assets, method

    return [], "UNMATCHED"


def users_for_holding(holding):
    if holding.owner_id:
        return [holding.owner]
    if holding.family_id:
        return list(
            User.objects.filter(
                profile__family_groups=holding.family,
                is_active=True,
            ).distinct()
        )
    return []


def match_user_watchlists(filing):
    q = WatchListEntry.objects.filter(
        product__is_active=True
    ).select_related("user", "product")

    filters = Q()
    if filing.isin:
        filters |= Q(product__isin__iexact=filing.isin)
    if filing.symbol:
        filters |= Q(product__external_identifier__iexact=filing.symbol)
    if filing.bse_code:
        filters |= Q(product__external_identifier__iexact=filing.bse_code)
    if filing.company_name:
        filters |= Q(product__name__iexact=filing.company_name)

    return list(q.filter(filters)) if filters else []
