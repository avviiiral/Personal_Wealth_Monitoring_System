from datetime import date, timedelta

from investments.models import Transaction
from market_data.models import DataSource, MarketPrice
from mutual_funds.services.amfi import AMFIService


class MutualFundHistoryBackfillService:
    """
    Backfills historical AMFI NAV data for MUTUAL_FUND Assets,
    starting from each asset's earliest transaction (buy) date,
    and stores it directly on market_data.MarketPrice (matched
    by ISIN), the same table STOCK/ETF/BOND prices live in.

    AMFI's historical NAV download returns ALL schemes for a
    date range in one response (not one scheme at a time), so
    each date-range "chunk" is downloaded once and then matched
    against every requested asset's ISIN, rather than once per
    asset.

    AMFI's historical endpoint supports a maximum 90-day window
    per request, so the full required range is split into
    consecutive <=90-day chunks.
    """

    CHUNK_DAYS = 90

    @classmethod
    def _date_chunks(cls, start_date, end_date):
        chunks = []

        current = start_date

        while current <= end_date:

            chunk_end = min(
                current + timedelta(days=cls.CHUNK_DAYS - 1),
                end_date,
            )

            chunks.append((current, chunk_end))

            current = chunk_end + timedelta(days=1)

        return chunks

    @staticmethod
    def _earliest_transaction_date(asset):
        return (
            Transaction.objects
            .filter(asset=asset)
            .order_by("transaction_date")
            .values_list(
                "transaction_date",
                flat=True,
            )
            .first()
        )

    @classmethod
    def backfill_for_assets(cls, assets):
        """
        assets: iterable of Asset records (expected category
        MUTUAL_FUND, though any Asset with an ISIN is handled
        the same way).

        Returns a summary dict:
            {
                "assets": <number of assets with a resolvable
                           earliest transaction date and ISIN>,
                "records_written": <MarketPrice rows created
                                     or updated>,
                "chunks": <number of AMFI historical requests
                            made>,
            }
        """

        asset_by_isin = {}
        earliest_by_asset_id = {}

        overall_start = None

        today = date.today()

        for asset in assets:

            isin = (
                asset.isin.strip().upper()
                if asset.isin
                else ""
            )

            if not isin:
                continue

            earliest_transaction_date = (
                cls._earliest_transaction_date(
                    asset
                )
            )

            if earliest_transaction_date is None:
                continue

            earliest_by_asset_id[asset.id] = (
                earliest_transaction_date
            )

            asset_by_isin.setdefault(
                isin,
                [],
            ).append(asset)

            if (
                overall_start is None
                or earliest_transaction_date
                < overall_start
            ):
                overall_start = (
                    earliest_transaction_date
                )

        if (
            overall_start is None
            or not asset_by_isin
        ):
            return {
                "assets": 0,
                "records_written": 0,
                "chunks": 0,
            }

        chunks = cls._date_chunks(
            overall_start,
            today,
        )

        records_written = 0

        for chunk_start, chunk_end in chunks:

            try:
                text = (
                    AMFIService
                    .download_historical_nav(
                        chunk_start,
                        chunk_end,
                    )
                )

            except Exception:
                # A single failed chunk (network hiccup, AMFI
                # downtime) should not stop the rest of the
                # backfill from completing.
                continue

            records = (
                AMFIService
                .parse_nav_file(
                    text,
                    historical=True,
                )
            )

            for record in records:

                isin = (
                    record["isin_growth"]
                    or record["isin_dividend"]
                )

                if not isin:
                    continue

                matching_assets = (
                    asset_by_isin.get(
                        isin.strip().upper()
                    )
                )

                if not matching_assets:
                    continue

                if record["date"] is None:
                    continue

                for asset in matching_assets:

                    earliest_transaction_date = (
                        earliest_by_asset_id[
                            asset.id
                        ]
                    )

                    if (
                        record["date"]
                        < earliest_transaction_date
                    ):
                        continue

                    MarketPrice.objects.update_or_create(
                        asset=asset,
                        date=record["date"],
                        source=DataSource.AMFI,
                        defaults={
                            "open_price": None,
                            "high_price": None,
                            "low_price": None,
                            "close_price": record[
                                "nav"
                            ],
                            "adjusted_close": record[
                                "nav"
                            ],
                            "volume": None,
                        },
                    )

                    records_written += 1

        return {
            "assets": len(earliest_by_asset_id),
            "records_written": records_written,
            "chunks": len(chunks),
        }