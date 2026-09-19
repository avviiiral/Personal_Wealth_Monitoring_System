import logging
from functools import lru_cache

import yfinance as yf

from market_data.services.security_resolver import SecurityResolver

logger = logging.getLogger(__name__)


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
    """Resolve sector and market-cap metadata for uploaded underlying names."""

    @staticmethod
    @lru_cache(maxsize=512)
    def _lookup(stock_name):
        name = str(stock_name or "").strip()
        if not name:
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
    def classify(cls, stock_name):
        return cls._lookup(str(stock_name or "").strip())
