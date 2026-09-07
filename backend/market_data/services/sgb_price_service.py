import re
from datetime import date

from curl_cffi import requests


class SGBPriceService:
    """
    Fetches Sovereign Gold Bond prices.

    Primary source:
        NSE

    Fallback source:
        Public SGB tracker

    SGBs are listed with symbols such as:

        SGBJUN29II
        SGBJAN30IX

    The Asset normally contains the ISIN and security name,
    so the NSE symbol is derived automatically.
    """

    NSE_HOME_URL = (
        "https://www.nseindia.com/"
    )

    NSE_QUOTE_URL = (
        "https://www.nseindia.com/"
        "api/quote-equity"
    )

    FALLBACK_URL = (
        "https://sgb.vercel.app/"
    )

    MONTHS = {
        "JAN": "JAN",
        "FEB": "FEB",
        "MAR": "MAR",
        "APR": "APR",
        "MAY": "MAY",
        "JUN": "JUN",
        "JUL": "JUL",
        "AUG": "AUG",
        "SEP": "SEP",
        "OCT": "OCT",
        "NOV": "NOV",
        "DEC": "DEC",
    }

    @classmethod
    def _get_session(cls):
        """
        Create an NSE session and obtain the required cookies.
        """

        session = requests.Session(
            impersonate="chrome",
            doh_url="https://1.1.1.1/dns-query",
        )

        headers = {
            "User-Agent": (
                "Mozilla/5.0 "
                "(Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 "
                "(KHTML, like Gecko) "
                "Chrome/151.0.0.0 Safari/537.36"
            ),
            "Accept": (
                "text/html,application/xhtml+xml,"
                "application/xml;q=0.9,*/*;q=0.8"
            ),
            "Accept-Language": (
                "en-US,en;q=0.9"
            ),
            "Referer": (
                "https://www.nseindia.com/"
            ),
        }

        response = session.get(
            cls.NSE_HOME_URL,
            headers=headers,
            timeout=20,
        )

        response.raise_for_status()

        return session, headers

    @classmethod
    def _normalize_roman_series(cls, value):
        """
        Convert series text such as:

            SR-II
            SR-IX
            SR  II

        into:

            II
            IX
        """

        if not value:
            return None

        value = str(value).upper().strip()

        value = re.sub(
            r"^SR[\s\-]*",
            "",
            value,
        )

        value = re.sub(
            r"[^IVXLCDM]",
            "",
            value,
        )

        return value or None

    @classmethod
    def derive_symbol(
        cls,
        name,
        isin=None,
    ):
        """
        Derive the NSE SGB symbol from the security name.

        Example:

        Sovereign Gold Bond 2.50% JUN 2029 SR-II 2021-22

        becomes:

        SGBJUN29II
        """

        if not name:
            return None

        text = str(name).upper().strip()

        # ------------------------------------------------------
        # SGB check
        # ------------------------------------------------------

        if (
            "SOVEREIGN GOLD BOND" not in text
            and not text.startswith("SGB")
        ):
            return None

        # ------------------------------------------------------
        # Already contains an NSE-style SGB symbol
        # ------------------------------------------------------

        existing_symbol = re.search(
            r"\bSGB[A-Z]{3}\d{2}[IVXLCDM]+\b",
            text,
        )

        if existing_symbol:
            return existing_symbol.group(0)

        # ------------------------------------------------------
        # Extract redemption month
        # ------------------------------------------------------

        month_match = re.search(
            r"\b"
            r"(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)"
            r"\b",
            text,
        )

        if not month_match:
            return None

        month = month_match.group(1)

        # ------------------------------------------------------
        # Extract redemption year
        # ------------------------------------------------------

        year_match = re.search(
            r"\b"
            + month
            + r"\s+"
            + r"(20\d{2})"
            + r"\b",
            text,
        )

        if not year_match:
            return None

        full_year = int(
            year_match.group(1)
        )

        year = str(full_year)[-2:]

        # ------------------------------------------------------
        # Extract SGB series
        # ------------------------------------------------------

        series_match = re.search(
            r"\bSR[\s\-]*([IVXLCDM]+)",
            text,
        )

        if not series_match:
            return None

        series = (
            series_match
            .group(1)
            .strip()
        )

        if not series:
            return None

        # ------------------------------------------------------
        # Build NSE symbol
        # ------------------------------------------------------

        return (
            f"SGB"
            f"{month}"
            f"{year}"
            f"{series}"
        )

    @classmethod
    def _get_nse_price(
        cls,
        symbol,
        isin,
        name,
    ):
        """
        Try to obtain the latest SGB price from NSE.

        Returns None when NSE is unavailable or
        rejects the request.
        """

        try:
            session, headers = (
                cls._get_session()
            )

            response = session.get(
                cls.NSE_QUOTE_URL,
                params={
                    "symbol": symbol,
                },
                headers=headers,
                timeout=20,
            )

            response.raise_for_status()

            data = response.json()

            price_info = data.get(
                "priceInfo",
                {},
            )

            # --------------------------------------------------
            # Prefer Last Price
            # --------------------------------------------------

            price = price_info.get(
                "lastPrice"
            )

            # --------------------------------------------------
            # Previous close fallback
            # --------------------------------------------------

            if price in (
                None,
                "",
                0,
                "0",
            ):
                price = price_info.get(
                    "previousClose"
                )

            if price in (
                None,
                "",
                0,
                "0",
            ):
                return None

            try:
                price = float(price)
            except (
                TypeError,
                ValueError,
            ):
                return None

            return {
                "symbol": symbol,
                "isin": isin,
                "price": price,
                "date": date.today(),
                "security_name": (
                    data.get(
                        "info",
                        {},
                    ).get(
                        "companyName"
                    )
                    or name
                ),
                "source": "NSE",
            }

        except Exception:
            return None

    @classmethod
    def _get_fallback_price(
        cls,
        symbol,
        isin,
        name,
    ):
        """
        Fallback SGB price source.

        The fallback page contains current SGB
        prices including the NSE symbol.

        Returns None when the source cannot be
        reached or the symbol cannot be found.
        """

        try:

            response = requests.get(
                cls.FALLBACK_URL,
                impersonate="chrome",
                timeout=20,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 "
                        "(Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 "
                        "(KHTML, like Gecko) "
                        "Chrome/151.0.0.0 "
                        "Safari/537.36"
                    ),
                    "Accept": (
                        "text/html,application/xhtml+xml,"
                        "application/xml;q=0.9,*/*;q=0.8"
                    ),
                },
            )

            response.raise_for_status()

            html = response.text

            # --------------------------------------------------
            # Locate the symbol in the returned page.
            #
            # Example:
            #
            # SGBJUN29II ... ₹15780
            # --------------------------------------------------

            escaped_symbol = re.escape(
                symbol
            )

            pattern = re.compile(
                escaped_symbol
                + r".{0,1000}?"
                + r"₹\s*"
                + r"([\d,]+(?:\.\d+)?)",
                re.IGNORECASE
                | re.DOTALL,
            )

            match = pattern.search(
                html
            )

            if not match:
                return None

            price_text = (
                match.group(1)
                .replace(",", "")
                .strip()
            )

            try:
                price = float(
                    price_text
                )
            except (
                TypeError,
                ValueError,
            ):
                return None

            if price <= 0:
                return None

            return {
                "symbol": symbol,
                "isin": isin,
                "price": price,
                "date": date.today(),
                "security_name": name,
                "source": "SGB_FALLBACK",
            }

        except Exception:
            return None

    @classmethod
    def get_latest_price(
        cls,
        name,
        isin=None,
    ):
        """
        Fetch the current NSE price for an SGB.

        Source priority:

            1. NSE
            2. Public SGB fallback

        Returns:

        {
            "symbol": "SGBJUN29II",
            "isin": "IN0020210061",
            "price": ...,
            "date": ...,
            "security_name": ...,
            "source": ...
        }

        Returns None when the SGB symbol cannot
        be resolved or no source returns a price.
        """

        symbol = cls.derive_symbol(
            name=name,
            isin=isin,
        )

        if not symbol:
            return None

        # ======================================================
        # PRIMARY SOURCE: NSE
        # ======================================================

        nse_result = (
            cls._get_nse_price(
                symbol=symbol,
                isin=isin,
                name=name,
            )
        )

        if nse_result is not None:
            return nse_result

        # ======================================================
        # FALLBACK SOURCE
        # ======================================================

        fallback_result = (
            cls._get_fallback_price(
                symbol=symbol,
                isin=isin,
                name=name,
            )
        )

        if fallback_result is not None:
            return fallback_result

        return None