from datetime import datetime
from decimal import Decimal, InvalidOperation
import time

import requests

from django.db import transaction

from mutual_funds.models import (
    MutualFundNAV,
    MutualFundScheme,
)


class AMFIService:
    """
    Service for downloading and processing mutual-fund
    NAV data from AMFI.
    """

    NAV_URL = (
        "https://www.amfiindia.com/spages/NAVAll.txt"
    )

    NAV_HISTORY_URL = (
        "https://portal.amfiindia.com/"
        "DownloadNAVHistoryReport_Po.aspx"
    )

    @staticmethod
    def _headers():
        return {
            "User-Agent": (
                "Mozilla/5.0 "
                "(Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 "
                "(KHTML, like Gecko) "
                "Chrome/151.0 Safari/537.36"
            )
        }

    @staticmethod
    def download_latest_nav():
        """
        Download the latest NAV text file from AMFI.
        """

        response = requests.get(
            AMFIService.NAV_URL,
            headers=AMFIService._headers(),
            timeout=30,
        )

        response.raise_for_status()

        return response.text

    @staticmethod
    def download_historical_nav(
        from_date,
        to_date,
    ):
        """
        Download historical NAV data from AMFI.

        AMFI historical NAV downloads support a maximum
        period of 90 days at a time.
        """

        if from_date > to_date:
            raise ValueError(
                "From date cannot be after to date."
            )

        if (
            to_date - from_date
        ).days > 90:
            raise ValueError(
                "AMFI historical NAV download supports "
                "a maximum period of 90 days at a time."
            )

        response = requests.get(
            AMFIService.NAV_HISTORY_URL,
            params={
                "tp": "1",
                "frmdt": from_date.strftime(
                    "%d-%b-%Y"
                ),
                "todt": to_date.strftime(
                    "%d-%b-%Y"
                ),
            },
            headers=AMFIService._headers(),
            timeout=60,
        )

        response.raise_for_status()

        return response.text

    @staticmethod
    def _build_record(
        scheme_code,
        isin_first,
        isin_second,
        scheme_name,
        nav,
        nav_date,
    ):
        """
        Build a normalized AMFI NAV record.
        """

        scheme_name_lower = (
            scheme_name.lower()
        )

        isin_growth = None
        isin_dividend = None

        if (
            "growth" in scheme_name_lower
            and "idcw" not in scheme_name_lower
            and "dividend" not in scheme_name_lower
        ):

            if (
                isin_first
                and isin_first != "-"
            ):
                isin_growth = isin_first

        else:

            if (
                isin_first
                and isin_first != "-"
            ):
                isin_dividend = isin_first

            elif (
                isin_second
                and isin_second != "-"
            ):
                isin_dividend = isin_second

        return {
            "scheme_code": scheme_code,
            "isin_growth": isin_growth,
            "isin_dividend": isin_dividend,
            "scheme_name": scheme_name,
            "nav": nav,
            "date": nav_date,
        }

    @staticmethod
    def _parse_latest_record(parts):
        """
        Parse latest AMFI NAV format.

        0 = Scheme Code
        1 = ISIN Div Payout / ISIN Growth
        2 = ISIN Div Reinvestment
        3 = Scheme Name
        4 = Plan
        5 = Option
        6 = NAV
        7 = Date

        AMFI added the Plan/Option columns to this feed
        after this parser was originally written, which
        shifted NAV and Date two columns to the right.
        """

        if len(parts) < 8:
            return None

        scheme_code = parts[0]
        isin_first = parts[1]
        isin_second = parts[2]
        scheme_name = parts[3]
        nav_text = parts[6]
        date_text = parts[7]

        if not scheme_code.isdigit():
            return None

        if not scheme_name:
            return None

        try:
            nav = Decimal(nav_text)
        except (
            InvalidOperation,
            ValueError,
            TypeError,
        ):
            return None

        if nav < 0:
            return None

        try:
            nav_date = datetime.strptime(
                date_text,
                "%d-%b-%Y",
            ).date()
        except ValueError:
            return None

        return AMFIService._build_record(
            scheme_code=scheme_code,
            isin_first=isin_first,
            isin_second=isin_second,
            scheme_name=scheme_name,
            nav=nav,
            nav_date=nav_date,
        )

    @staticmethod
    def _parse_historical_record(parts):
        """
        Parse historical AMFI NAV format.

        0 = Scheme Code
        1 = Scheme Name
        2 = ISIN Div Payout / ISIN Growth
        3 = ISIN Div Reinvestment
        4 = NAV
        5 = Repurchase Price
        6 = Sale Price
        7 = Date
        """

        if len(parts) < 8:
            return None

        scheme_code = parts[0]
        scheme_name = parts[1]
        isin_first = parts[2]
        isin_second = parts[3]
        nav_text = parts[4]
        date_text = parts[-1]

        if not scheme_code.isdigit():
            return None

        if not scheme_name:
            return None

        try:
            nav = Decimal(nav_text)
        except (
            InvalidOperation,
            ValueError,
            TypeError,
        ):
            return None

        if nav < 0:
            return None

        try:
            nav_date = datetime.strptime(
                date_text,
                "%d-%b-%Y",
            ).date()
        except ValueError:
            return None

        return AMFIService._build_record(
            scheme_code=scheme_code,
            isin_first=isin_first,
            isin_second=isin_second,
            scheme_name=scheme_name,
            nav=nav,
            nav_date=nav_date,
        )

    @staticmethod
    def parse_nav_file(
        text,
        historical=False,
    ):
        """
        Parse AMFI NAV data.

        Supports both latest and historical formats.
        """

        records = []

        for raw_line in text.splitlines():

            line = raw_line.strip()

            if not line:
                continue

            if ";" not in line:
                continue

            parts = [
                part.strip()
                for part in line.split(";")
            ]

            if historical:

                record = (
                    AMFIService
                    ._parse_historical_record(
                        parts
                    )
                )

            else:

                record = (
                    AMFIService
                    ._parse_latest_record(
                        parts
                    )
                )

            if record:
                records.append(record)

        return records

    # Each batch commits as its own short transaction rather than
    # one giant transaction spanning the entire import (which can
    # be 14,000+ scheme/NAV upserts for a full AMFI file). A single
    # multi-minute transaction holds SQLite's write lock the whole
    # time, causing unrelated concurrent requests (login, dashboard
    # reads, user management) to fail with "database is locked"
    # even with a generous busy-timeout configured. Committing in
    # batches bounds the lock-hold time to a fraction of a second
    # per batch, letting other connections interleave, while each
    # batch is still atomic (no partial-batch corruption on error).
    NAV_IMPORT_BATCH_SIZE = 500

    # Pause between batch transactions so the write lock is
    # actually released for a moment before the next batch's
    # BEGIN. Per-batch atomics alone don't guarantee a waiting
    # connection (e.g. a manual-price PUT from another user) gets
    # a turn - if this loop reacquires the lock immediately, a
    # concurrent writer can lose the race on every retry within
    # its own busy_timeout window. Combined with the bulk_create
    # rewrite of _import_batch below, this keeps lock-hold time
    # per batch to milliseconds instead of seconds.
    NAV_IMPORT_BATCH_PAUSE_SECONDS = 0.1

    @staticmethod
    def _import_records(
        owner,
        records,
    ):
        """
        Import parsed NAV records in short, bounded-size
        transactions (see NAV_IMPORT_BATCH_SIZE) instead of one
        transaction for the whole file.

        Existing schemes are updated.
        Existing NAV records are updated rather
        than duplicated.
        """

        scheme_count = 0
        nav_count = 0

        batch = []

        for record in records:
            batch.append(record)

            if len(batch) >= AMFIService.NAV_IMPORT_BATCH_SIZE:
                batch_schemes, batch_navs = (
                    AMFIService._import_batch(owner, batch)
                )

                scheme_count += batch_schemes
                nav_count += batch_navs

                batch = []

                time.sleep(
                    AMFIService
                    .NAV_IMPORT_BATCH_PAUSE_SECONDS
                )

        if batch:
            batch_schemes, batch_navs = (
                AMFIService._import_batch(owner, batch)
            )

            scheme_count += batch_schemes
            nav_count += batch_navs

        return {
            "schemes": scheme_count,
            "nav_records": nav_count,
        }

    @staticmethod
    @transaction.atomic
    def _import_batch(
        owner,
        records,
    ):
        """
        Two bulk_create(update_conflicts) calls instead of ~2 ORM
        queries per record. Keeps each batch's write-lock hold
        time to milliseconds instead of seconds - this is what was
        starving concurrent writers (a manual-price PUT, another
        user's edit) during a full AMFI import.

        Field-preservation: a record that doesn't carry an
        isin_growth/isin_dividend value (AMFI rows only ever
        populate one of the two) must not blank out a value
        already stored for that scheme, so existing values are
        looked up first and only overwritten when the incoming
        record actually has something.
        """

        scheme_codes = [
            record["scheme_code"]
            for record in records
        ]

        existing_schemes = {
            scheme.scheme_code: scheme
            for scheme in (
                MutualFundScheme.objects
                .filter(
                    owner=owner,
                    scheme_code__in=scheme_codes,
                )
            )
        }

        # Keyed by scheme_code so a scheme_code repeated within
        # one batch naturally resolves to the last record's
        # values, same as the sequential update_or_create loop
        # this replaced - and so bulk_create never sees the same
        # conflict target twice in one call, which SQLite/
        # Postgres both reject.
        schemes_by_code = {}

        for record in records:

            scheme_code = record["scheme_code"]
            existing = existing_schemes.get(scheme_code)

            schemes_by_code[scheme_code] = MutualFundScheme(
                owner=owner,
                scheme_code=scheme_code,
                scheme_name=record["scheme_name"],
                isin_growth=(
                    record["isin_growth"]
                    or (
                        existing.isin_growth
                        if existing
                        else None
                    )
                ),
                isin_dividend=(
                    record["isin_dividend"]
                    or (
                        existing.isin_dividend
                        if existing
                        else None
                    )
                ),
            )

        MutualFundScheme.objects.bulk_create(
            list(schemes_by_code.values()),
            update_conflicts=True,
            unique_fields=["owner", "scheme_code"],
            update_fields=[
                "scheme_name",
                "isin_growth",
                "isin_dividend",
            ],
        )

        # bulk_create(update_conflicts=True) does not reliably
        # return primary keys for rows that hit the conflict path
        # (only freshly inserted rows are guaranteed one back), so
        # re-fetch scheme ids by code to build the NAV rows below
        # against the right scheme_id.
        scheme_ids_by_code = dict(
            MutualFundScheme.objects
            .filter(
                owner=owner,
                scheme_code__in=scheme_codes,
            )
            .values_list("scheme_code", "id")
        )

        navs_by_key = {}

        for record in records:

            scheme_id = scheme_ids_by_code.get(
                record["scheme_code"]
            )

            if scheme_id is None:
                # Should be unreachable - the scheme was just
                # written above - but never fabricate a NAV row
                # against a scheme that doesn't actually exist.
                continue

            nav_key = (scheme_id, record["date"])

            navs_by_key[nav_key] = MutualFundNAV(
                scheme_id=scheme_id,
                date=record["date"],
                source="AMFI",
                nav=record["nav"],
            )

        MutualFundNAV.objects.bulk_create(
            list(navs_by_key.values()),
            update_conflicts=True,
            unique_fields=["scheme", "date", "source"],
            update_fields=["nav"],
        )

        # Counts records processed, matching the old loop's
        # semantics (it incremented both counters once per record
        # regardless of create vs. update) - not the number of
        # distinct rows bulk_create actually wrote.
        return len(records), len(records)

    @staticmethod
    def import_latest_navs(owner):
        """
        Download the latest AMFI NAV file and import
        all valid scheme records.
        """

        text = (
            AMFIService
            .download_latest_nav()
        )

        records = (
            AMFIService
            .parse_nav_file(
                text,
                historical=False,
            )
        )

        return AMFIService._import_records(
            owner,
            records,
        )

    @staticmethod
    def import_historical_navs(
        owner,
        from_date,
        to_date,
    ):
        """
        Download and import historical AMFI NAV data.
        """

        text = (
            AMFIService
            .download_historical_nav(
                from_date,
                to_date,
            )
        )

        records = (
            AMFIService
            .parse_nav_file(
                text,
                historical=True,
            )
        )

        return AMFIService._import_records(
            owner,
            records,
        )