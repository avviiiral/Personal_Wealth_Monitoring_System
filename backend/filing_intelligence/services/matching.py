from django.db.models import Q
from django.contrib.auth.models import User
from investments.models import Asset, AssetCategory, Holding
from watchlist.models import WatchListEntry

def match_assets(filing):
    base=Asset.objects.filter(is_active=True,category=AssetCategory.STOCK).select_related("security_master")
    if filing.isin:
        assets=list(base.filter(isin__iexact=filing.isin))
        if assets: return assets,"ISIN"
    if filing.symbol:
        assets=list(base.filter(symbol__iexact=filing.symbol))
        if assets: return assets,"EXCHANGE_SYMBOL"
    if filing.bse_code:
        assets=list(base.filter(symbol__iexact=filing.bse_code))
        if assets: return assets,"BSE_CODE"
    if filing.company_name:
        exact=list(base.filter(Q(name__iexact=filing.company_name)|Q(security_master__asset_name__iexact=filing.company_name)))
        if exact: return exact,"COMPANY_NAME"
    return [],"UNMATCHED"

def users_for_holding(holding):
    if holding.owner_id: return [holding.owner]
    if holding.family_id: return list(User.objects.filter(profile__family_groups=holding.family,is_active=True).distinct())
    return []

def match_user_watchlists(filing):
    q=WatchListEntry.objects.filter(product__is_active=True).select_related("user","product")
    filters=Q()
    if filing.isin: filters |= Q(product__isin__iexact=filing.isin)
    if filing.symbol: filters |= Q(product__external_identifier__iexact=filing.symbol)
    if filing.bse_code: filters |= Q(product__external_identifier__iexact=filing.bse_code)
    if filing.company_name: filters |= Q(product__name__iexact=filing.company_name)
    return list(q.filter(filters)) if filters else []
