"""Security-master-driven exchange filing discovery.

The portfolio's family-scoped Asset/SecurityMaster records are the canonical
identity inputs. Yahoo symbols and pricing availability are deliberately not
used as identity evidence.
"""
import logging
from datetime import datetime, time, timedelta

from django.utils import timezone

from investments.models import Asset, AssetCategory
from filing_intelligence.models import Filing
from filing_intelligence.providers.official import NSEFilingProvider
from filing_intelligence.services.pipeline import _process
from filing_intelligence.providers.base import NormalizedFiling

logger = logging.getLogger(__name__)

LISTED_CATEGORIES = (AssetCategory.STOCK, AssetCategory.ETF)


def _asset_identity(asset):
    """Return a verified NSE identity only when an ISIN is available."""
    isin = str(asset.isin or getattr(asset.security_master, "isin", "") or "").strip().upper()
    symbol = str(asset.symbol or "").strip().upper()
    # Resolver-generated Yahoo tickers are useful for pricing, not exchange
    # identity. Strip only known Yahoo suffixes; never guess an NSE symbol
    # from a display name.
    for suffix in (".NS", ".BO"):
        if symbol.endswith(suffix):
            symbol = symbol[:-len(suffix)]
            break
    if not isin:
        return None
    return {"isin": isin, "symbol": symbol}


def discover_filings_for_assets(*, asset_ids, since=None, until=None, dry_run=False, provider=None):
    """Fetch NSE announcements for imported canonical assets and persist idempotently."""
    assets = (
        Asset.objects.filter(id__in=asset_ids, is_active=True, category__in=LISTED_CATEGORIES)
        .select_related("security_master")
        .order_by("id")
    )
    identities = {}
    for asset in assets:
        identity = _asset_identity(asset)
        if identity:
            identities.setdefault(identity["isin"], []).append((asset, identity))

    if not identities:
        return {"assets_considered": 0, "identities_resolved": 0, "filings_fetched": 0,
                "filings_stored": 0, "duplicates": 0, "unresolved_assets": assets.count()}

    now = timezone.now()
    local_tz = timezone.get_current_timezone()
    start = since or (now - timedelta(days=7))
    end = until or now
    if isinstance(start, datetime) and timezone.is_naive(start):
        start = timezone.make_aware(start, local_tz)
    if isinstance(end, datetime) and timezone.is_naive(end):
        end = timezone.make_aware(end, local_tz)
    if not isinstance(start, datetime):
        start = timezone.make_aware(datetime.combine(start, time.min), local_tz)
    if not isinstance(end, datetime):
        end = timezone.make_aware(datetime.combine(end + timedelta(days=1), time.min), local_tz)
    if start > end:
        raise ValueError("since must not be later than until")

    stats = {"assets_considered": assets.count(), "identities_resolved": len(identities),
             "filings_fetched": 0, "filings_stored": 0, "duplicates": 0,
             "unresolved_assets": assets.count() - sum(len(v) for v in identities.values())}
    provider = provider or NSEFilingProvider()
    try:
        try:
            items = list(provider.fetch_filings(start, until=end))
        except TypeError:
            # Keep compatibility with existing providers that expose only
            # fetch_filings(since); bound the returned data below.
            items = list(provider.fetch_filings(start))
    except Exception:
        logger.exception("Security-driven NSE filing discovery failed")
        stats["provider_failures"] = 1
        return stats

    matching_items = []
    for item in items:
        if item.published_at < start or item.published_at >= end:
            continue
        if item.isin and item.isin.strip().upper() in identities:
            matching_items.append(item)
            continue
        symbol = str(item.symbol or "").strip().upper()
        for records in identities.values():
            if symbol and any(identity["symbol"] == symbol for _, identity in records):
                matching_items.append(item)
                break

    stats["filings_fetched"] = len(matching_items)
    for item in matching_items:
        result = _process(item, dry_run=dry_run)
        stats["duplicates"] += int(result["duplicate"])
        stats["filings_stored"] += int(result["stored"])
    return stats
