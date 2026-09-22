import logging
import re
from functools import lru_cache

import yfinance as yf
from django.db.models import Q

from investments.models import Asset, SecurityMaster
from market_data.services.security_resolver import SecurityResolver

logger = logging.getLogger(__name__)


def _normalize_name(value):
    value = str(value or "").strip().upper()
    value = re.sub(r"[.&,'’`]", " ", value)
    value = re.sub(r"\b(LIMITED|LTD|PRIVATE|PVT|PLC)\b", " ", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def _compact_name(value):
    return re.sub(r"[^A-Z0-9]", "", _normalize_name(value))


def _cap_type_from_market_cap(market_cap):
    """Use the same market-cap bands as the existing Yahoo enrichment."""
    if market_cap is None:
        return None

    try:
        value = float(market_cap)
    except (TypeError, ValueError):
        return None

    if value >= 1_000_000_000_000:
        return "Large Cap"
    if value >= 200_000_000_000:
        return "Mid Cap"
    return "Small Cap"


class UnderlyingSecurityClassifier:
    """Resolve sector, market cap and ISIN for uploaded underlying names."""

    @staticmethod
    @lru_cache(maxsize=512)
    def _lookup(stock_name):
        name = str(stock_name or "").strip()
        if not name or name.casefold() == "unclassified":
            return None, None

        candidates = []

        try:
            resolved = SecurityResolver.resolve_yahoo_symbol(name=name)
            if resolved:
                candidates.append(resolved)
        except Exception:
            pass

        try:
            search = yf.Search(name, max_results=10)
            for quote in getattr(search, "quotes", []) or []:
                symbol = str(quote.get("symbol") or "").strip().upper()
                if symbol.endswith((".NS", ".BO")) and symbol not in candidates:
                    candidates.append(symbol)
        except Exception:
            logger.warning(
                "[UNDERLYING CLASSIFICATION] Yahoo search failed for %s",
                name,
                exc_info=True,
            )

        for symbol in candidates:
            try:
                info = yf.Ticker(symbol).info or {}
            except Exception:
                logger.warning(
                    "[UNDERLYING CLASSIFICATION] Yahoo info lookup failed for %s (%s)",
                    name,
                    symbol,
                    exc_info=True,
                )
                continue

            sector = str(info.get("sector") or "").strip() or None
            cap_type = _cap_type_from_market_cap(info.get("marketCap"))

            if sector or cap_type:
                return sector, cap_type

        return None, None

    @classmethod
    @lru_cache(maxsize=512)
    def resolve_isin(cls, stock_name):
        """Resolve an underlying security to its canonical ISIN."""
        name = str(stock_name or "").strip()
        if not name or name.casefold() == "unclassified":
            return None

        normalized = _normalize_name(name)
        compact = _compact_name(name)

        # Match SecurityMaster by normalized/compact name so
        # "ICICI Bank" and "ICICI Bank Ltd." resolve to the same security.
        try:
            masters = (
                SecurityMaster.objects
                .exclude(isin__isnull=True)
                .exclude(isin__exact="")
                .only("asset_name", "isin")
            )
            for master in masters:
                master_compact = _compact_name(master.asset_name)
                if not master_compact:
                    continue
                if _normalize_name(master.asset_name) == normalized or master_compact == compact:
                    return str(master.isin).strip().upper()
        except Exception:
            logger.warning(
                "[UNDERLYING ISIN] SecurityMaster normalized lookup failed for %s",
                name,
                exc_info=True,
            )

        # Match Asset names using the same normalization.
        try:
            assets = (
                Asset.objects
                .filter(Q(isin__isnull=False))
                .exclude(isin__exact="")
                .only("name", "isin")
            )
            for asset in assets:
                asset_compact = _compact_name(asset.name)
                if not asset_compact:
                    continue
                if _normalize_name(asset.name) == normalized or asset_compact == compact:
                    return str(asset.isin).strip().upper()
        except Exception:
            logger.warning(
                "[UNDERLYING ISIN] Asset normalized lookup failed for %s",
                name,
                exc_info=True,
            )

        candidates = []

        try:
            resolved = SecurityResolver.resolve_yahoo_symbol(name=name)
            if resolved:
                candidates.append(resolved)
        except Exception:
            pass

        try:
            search = yf.Search(name, max_results=10)
            for quote in getattr(search, "quotes", []) or []:
                symbol = str(quote.get("symbol") or "").strip().upper()
                if symbol.endswith((".NS", ".BO")) and symbol not in candidates:
                    candidates.append(symbol)
        except Exception:
            logger.warning(
                "[UNDERLYING ISIN] Yahoo search failed for %s",
                name,
                exc_info=True,
            )

        for symbol in candidates:
            try:
                info = yf.Ticker(symbol).info or {}
                isin = str(info.get("isin") or "").strip().upper()
                if isin:
                    return isin
            except Exception:
                logger.warning(
                    "[UNDERLYING ISIN] Yahoo info lookup failed for %s (%s)",
                    name,
                    symbol,
                    exc_info=True,
                )

        return None

    @classmethod
    def classify(cls, stock_name):
        return cls._lookup(str(stock_name or "").strip())

    @classmethod
    def resolve_metadata(cls, stock_name):
        """Resolve ISIN, sector and market-cap metadata from canonical/public sources."""
        name = str(stock_name or "").strip()
        if not name or name.casefold() == "unclassified":
            return None, None, None

        normalized = _normalize_name(name)
        compact = _compact_name(name)

        # Prefer a canonical SecurityMaster/Asset record for the security itself.
        try:
            master = (
                SecurityMaster.objects
                .exclude(isin__isnull=True)
                .exclude(isin__exact="")
                .filter(
                    Q(asset_name__iexact=name)
                    | Q(asset_name__iexact=normalized)
                )
                .only("asset_name", "isin", "sector", "cap_type")
                .first()
            )
            if master:
                isin = str(master.isin or "").strip().upper() or None
                sector = str(master.sector or "").strip() or None
                cap_type = str(master.cap_type or "").strip() or None
                if isin and sector and cap_type:
                    return isin, sector, cap_type
        except Exception:
            logger.warning("[UNDERLYING METADATA] SecurityMaster lookup failed for %s", name, exc_info=True)

        # Normalized/compact Asset lookup handles Ltd./Limited suffix differences.
        try:
            assets = (
                Asset.objects
                .exclude(isin__isnull=True)
                .exclude(isin__exact="")
                .only("name", "isin", "security_master_id")
            )
            for asset in assets:
                if _normalize_name(asset.name) != normalized and _compact_name(asset.name) != compact:
                    continue
                isin = str(asset.isin or "").strip().upper() or None
                sm = getattr(asset, "security_master", None)
                sector = str(getattr(sm, "sector", "") or "").strip() or None
                cap_type = str(getattr(sm, "cap_type", "") or "").strip() or None
                if isin:
                    if sector and cap_type:
                        return isin, sector, cap_type
                    yahoo_sector, yahoo_cap = cls.classify(name)
                    return isin, sector or yahoo_sector, cap_type or yahoo_cap
        except Exception:
            logger.warning("[UNDERLYING METADATA] Asset lookup failed for %s", name, exc_info=True)

        # Existing classifier can resolve many names directly through Yahoo.
        yahoo_sector, yahoo_cap = cls.classify(name)
        yahoo_isin = cls.resolve_isin(name)
        if yahoo_isin or yahoo_sector or yahoo_cap:
            return yahoo_isin, yahoo_sector, yahoo_cap

        return None, None, None
