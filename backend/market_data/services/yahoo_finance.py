import logging
from decimal import Decimal, InvalidOperation

import pandas as pd
import yfinance as yf
from curl_cffi import requests

from django.db import transaction

from market_data.models import MarketPrice, DataSource


# ============================================================
# YAHOO FINANCE LOGGING
# ============================================================

yfinance_logger = logging.getLogger("yfinance")
yfinance_logger.setLevel(logging.CRITICAL)
yfinance_logger.propagate = False


class YahooFinanceService:
    """
    Service responsible for downloading historical market data
    from Yahoo Finance and storing it in the PWMS database.
    """

    @staticmethod
    def _to_decimal(value):
        """
        Safely convert a value to Decimal.
        """

        if pd.isna(value):
            return None

        try:
            return Decimal(str(value))

        except (InvalidOperation, ValueError, TypeError):
            return None

    @staticmethod
    def fetch_history(
        symbol,
        period="1y",
        interval="1d",
        start=None,
        end=None,
    ):
        """
        Download historical market data from Yahoo Finance.

        For the initial fetch, use:

            period="1y"

        For incremental updates, use:

            start=<latest stored date>

        and optionally:

            end=<tomorrow>
        """

        if not symbol:
            raise ValueError(
                "Yahoo Finance symbol is required."
            )

        try:
            session = requests.Session(
                impersonate="chrome",
                doh_url="https://1.1.1.1/dns-query",
            )

            ticker = yf.Ticker(
                symbol,
                session=session,
            )

            kwargs = {
                "interval": interval,
                "auto_adjust": False,
            }

            # --------------------------------------------------
            # Incremental fetch
            # --------------------------------------------------

            if start is not None:
                kwargs["start"] = start

                if end is not None:
                    kwargs["end"] = end

            # --------------------------------------------------
            # Initial/history fetch
            # --------------------------------------------------

            else:
                kwargs["period"] = period

            data = ticker.history(**kwargs)

        except Exception as exc:
            raise ValueError(
                f"Unable to fetch market data for "
                f"{symbol}: {exc}"
            ) from exc

        if data is None or data.empty:
            raise ValueError(
                f"No market data returned for symbol: {symbol}"
            )

        return data

    @staticmethod
    @transaction.atomic
    def save_history(
        asset,
        symbol,
        period="1y",
        start=None,
        end=None,
    ):
        """
        Fetch Yahoo Finance data and store it against an Asset.

        Existing records for the same asset/date/source are updated
        instead of duplicated.
        """

        data = YahooFinanceService.fetch_history(
            symbol=symbol,
            period=period,
            interval="1d",
            start=start,
            end=end,
        )

        saved_count = 0

        for index, row in data.iterrows():

            market_date = index.date()

            MarketPrice.objects.update_or_create(
                asset=asset,
                date=market_date,
                source=DataSource.YAHOO_FINANCE,
                defaults={
                    "open_price": YahooFinanceService._to_decimal(
                        row.get("Open")
                    ),

                    "high_price": YahooFinanceService._to_decimal(
                        row.get("High")
                    ),

                    "low_price": YahooFinanceService._to_decimal(
                        row.get("Low")
                    ),

                    "close_price": YahooFinanceService._to_decimal(
                        row.get("Close")
                    ),

                    "adjusted_close": YahooFinanceService._to_decimal(
                        row.get("Adj Close")
                    ),

                    "volume": (
                        int(row["Volume"])
                        if not pd.isna(row.get("Volume"))
                        else None
                    ),
                },
            )

            saved_count += 1

        if saved_count == 0:
            raise ValueError(
                f"No usable market-price records returned "
                f"for symbol: {symbol}"
            )

        return saved_count