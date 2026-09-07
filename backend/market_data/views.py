import logging

from curl_cffi import requests

from django.http import JsonResponse
from django.views.decorators.http import require_GET


logger = logging.getLogger(__name__)


YAHOO_SEARCH_URL = (
    "https://query1.finance.yahoo.com/v1/finance/search"
)

YAHOO_QUOTE_URL = (
    "https://query1.finance.yahoo.com/v7/finance/quote"
)

NSE_QUOTE_URL = (
    "https://www.nseindia.com/api/quote-equity"
)


# ==========================================================
# COMMON HEADERS
# ==========================================================

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/151.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "application/json,text/plain,*/*"
    ),
}


# ==========================================================
# KNOWN INDIAN ISIN FALLBACK
# ==========================================================

# This is only a fallback for securities where the external
# market-data provider does not return an ISIN.
#
# More securities can be added here later if required.
KNOWN_ISINS = {
    "DCBBANK": "INE503A01015",
    "TCS": "INE467B01029",
    "INFY": "INE009A01021",
    "RELIANCE": "INE002A01018",
    "HDFCBANK": "INE040A01034",
    "ICICIBANK": "INE090A01021",
    "SBIN": "INE062A01020",
    "ITC": "INE154A01025",
    "LT": "INE018A01030",
    "AXISBANK": "INE238A01034",
    "KOTAKBANK": "INE237A01028",
    "BHARTIARTL": "INE397D01024",
    "MARUTI": "INE585B01010",
    "TATAMOTORS": "INE155A01022",
    "TATASTEEL": "INE081A01020",
    "SUNPHARMA": "INE044A01036",
    "HINDUNILVR": "INE030A01027",
    "BAJFINANCE": "INE296A01024",
    "ASIANPAINT": "INE021A01026",
    "WIPRO": "INE075A01022",
}


# ==========================================================
# NORMALIZE SYMBOL
# ==========================================================

def _clean_symbol(symbol: str) -> str:
    if not symbol:
        return ""

    symbol = str(symbol).strip().upper()

    if symbol.endswith(".NS"):
        symbol = symbol[:-3]

    elif symbol.endswith(".BO"):
        symbol = symbol[:-3]

    return symbol


# ==========================================================
# KNOWN ISIN LOOKUP
# ==========================================================

def _get_known_isin(symbol: str):
    clean_symbol = _clean_symbol(symbol)

    return KNOWN_ISINS.get(clean_symbol)


# ==========================================================
# NSE ISIN LOOKUP
# ==========================================================

def _get_isin_from_nse(symbol: str):
    """
    Get ISIN from NSE for NSE-listed securities.
    """

    clean_symbol = _clean_symbol(symbol)

    if not clean_symbol:
        return None

    session = requests.Session(
        impersonate="chrome"
    )

    headers = {
        **BROWSER_HEADERS,
        "Referer": "https://www.nseindia.com/",
        "Accept-Language": "en-US,en;q=0.9",
    }

    try:
        # Establish NSE cookies first.
        session.get(
            "https://www.nseindia.com/",
            headers=headers,
            timeout=4,
        )

        response = session.get(
            NSE_QUOTE_URL,
            params={
                "symbol": clean_symbol,
            },
            headers=headers,
            timeout=4,
        )

        response.raise_for_status()

        data = response.json()

        # --------------------------------------------------
        # NSE normally exposes ISIN in metadata.
        # --------------------------------------------------

        metadata = data.get(
            "metadata",
            {},
        )

        isin = metadata.get("isin")

        if isin:
            return str(isin).strip().upper()

        # --------------------------------------------------
        # Fallback to info.
        # --------------------------------------------------

        info = data.get(
            "info",
            {},
        )

        isin = info.get("isin")

        if isin:
            return str(isin).strip().upper()

        # --------------------------------------------------
        # Fallback to securityInfo.
        # --------------------------------------------------

        security_info = data.get(
            "securityInfo",
            {},
        )

        isin = security_info.get("isin")

        if isin:
            return str(isin).strip().upper()

    except Exception as exc:
        logger.warning(
            "NSE ISIN lookup failed for %s: %s",
            clean_symbol,
            exc,
        )

    finally:
        try:
            session.close()
        except Exception:
            pass

    return None


# ==========================================================
# YAHOO ISIN LOOKUP
# ==========================================================

def _get_isin_from_yahoo(symbol: str):
    """
    Yahoo Finance sometimes provides ISIN through the
    ticker metadata endpoint.
    """

    if not symbol:
        return None

    session = requests.Session(
        impersonate="chrome"
    )

    try:
        response = session.get(
            YAHOO_QUOTE_URL,
            params={
                "symbols": symbol,
            },
            headers=BROWSER_HEADERS,
            timeout=4,
        )

        response.raise_for_status()

        data = response.json()

        quote_results = (
            data
            .get("quoteResponse", {})
            .get("result", [])
        )

        if not quote_results:
            return None

        quote = quote_results[0]

        isin = quote.get("isin")

        if isin:
            return str(isin).strip().upper()

    except Exception as exc:
        logger.warning(
            "Yahoo ISIN lookup failed for %s: %s",
            symbol,
            exc,
        )

    finally:
        try:
            session.close()
        except Exception:
            pass

    return None


# ==========================================================
# RESOLVE ISIN
# ==========================================================

def _resolve_isin(
    symbol: str,
    exchange: str = "",
):
    """
    Resolve ISIN using multiple sources.

    Priority:

    1. Yahoo search result
    2. Known Indian ISIN
    3. NSE
    4. Yahoo quote endpoint
    """

    if not symbol:
        return None

    clean_symbol = _clean_symbol(symbol)

    # ------------------------------------------------------
    # 1. Yahoo search result ISIN
    # ------------------------------------------------------

    # Caller may already have a Yahoo ISIN.
    # This is handled outside this function when available.

    # ------------------------------------------------------
    # 2. Known Indian security mapping
    # ------------------------------------------------------

    known_isin = _get_known_isin(clean_symbol)

    if known_isin:
        return known_isin

    # ------------------------------------------------------
    # 3. NSE lookup
    # ------------------------------------------------------

    exchange_upper = str(
        exchange or ""
    ).upper()

    if (
        exchange_upper == "NSE"
        or symbol.upper().endswith(".NS")
    ):
        isin = _get_isin_from_nse(
            clean_symbol
        )

        if isin:
            return isin

    # ------------------------------------------------------
    # 4. Yahoo quote fallback
    # ------------------------------------------------------

    isin = _get_isin_from_yahoo(
        symbol
    )

    if isin:
        return isin

    return None


# ==========================================================
# STOCK SEARCH
# ==========================================================

@require_GET
def stock_search(request):
    """
    Search stocks / ETFs.

    Yahoo Finance is used for discovery.

    ISIN is resolved separately because Yahoo's search
    response does not reliably contain ISIN.
    """

    search = (
        request.GET
        .get("search", "")
        .strip()
    )

    investment_type = (
        request.GET
        .get("type", "STOCK")
        .strip()
        .upper()
    )

    # ------------------------------------------------------
    # Minimum search length
    # ------------------------------------------------------

    if len(search) < 2:
        return JsonResponse({
            "count": 0,
            "results": [],
        })

    if investment_type not in {
        "STOCK",
        "ETF",
    }:
        investment_type = "STOCK"

    session = requests.Session(
        impersonate="chrome"
    )

    try:
        # ==================================================
        # YAHOO SEARCH
        # ==================================================

        response = session.get(
            YAHOO_SEARCH_URL,
            params={
                "q": search,
                "quotesCount": 20,
                "newsCount": 0,
                "enableFuzzyQuery": "true",
            },
            headers=BROWSER_HEADERS,
            timeout=5,
        )

        response.raise_for_status()

        data = response.json()

        quotes = data.get(
            "quotes",
            [],
        )

        results = []

        # ==================================================
        # PROCESS RESULTS
        # ==================================================

        for quote in quotes:

            quote_type = str(
                quote.get("quoteType") or ""
            ).upper()

            # --------------------------------------------------
            # STOCK FILTER
            # --------------------------------------------------

            if investment_type == "STOCK":

                if quote_type not in {
                    "EQUITY",
                    "COMMONSTOCK",
                }:
                    continue

            # --------------------------------------------------
            # ETF FILTER
            # --------------------------------------------------

            elif investment_type == "ETF":

                if quote_type != "ETF":
                    continue

            # --------------------------------------------------
            # SYMBOL
            # --------------------------------------------------

            symbol = (
                quote.get("symbol")
                or quote.get("ticker")
                or ""
            ).strip()

            if not symbol:
                continue

            # --------------------------------------------------
            # NAME
            # --------------------------------------------------

            name = (
                quote.get("longname")
                or quote.get("longName")
                or quote.get("shortname")
                or quote.get("shortName")
                or quote.get("name")
                or symbol
            ).strip()

            # --------------------------------------------------
            # EXCHANGE
            # --------------------------------------------------

            exchange = (
                quote.get("exchange")
                or quote.get("exchDisp")
                or ""
            ).strip()

            # --------------------------------------------------
            # CURRENCY
            # --------------------------------------------------

            currency = (
                quote.get("currency")
                or "INR"
            ).strip()

            # --------------------------------------------------
            # SHORT NAME
            # --------------------------------------------------

            short_name = (
                quote.get("shortname")
                or quote.get("shortName")
                or name
            ).strip()

            # ==================================================
            # ISIN
            # ==================================================

            # First take ISIN directly from Yahoo search.
            isin = quote.get("isin")

            if isin:
                isin = str(
                    isin
                ).strip().upper()

            # --------------------------------------------------
            # If Yahoo did not provide ISIN,
            # resolve it separately.
            # --------------------------------------------------

            if not isin:

                isin = _resolve_isin(
                    symbol=symbol,
                    exchange=exchange,
                )

            # ==================================================
            # RESULT
            # ==================================================

            results.append({
                "symbol": symbol,

                "name": name,

                "short_name": short_name,

                "exchange": exchange,

                "quote_type": quote_type,

                "isin": isin,

                "currency": currency,
            })

            # Keep maximum 10 results.
            if len(results) >= 10:
                break

        return JsonResponse({
            "count": len(results),
            "results": results,
        })

    except Exception as exc:

        logger.exception(
            "Yahoo Finance stock search failed for '%s': %s",
            search,
        )

        return JsonResponse({
            "count": 0,
            "results": [],
        })

    finally:

        try:
            session.close()
        except Exception:
            pass