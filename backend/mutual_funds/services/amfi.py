from datetime import datetime
from decimal import Decimal, InvalidOperation
import logging
import time

import requests

from django.db import transaction
from django.db.models import OuterRef, Subquery

from mutual_funds.models import (
    AMFIMasterNAV,
    AMFIMasterScheme,
    MutualFundNAV,
    MutualFundScheme,
)


logger = logging.getLogger(__name__)


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
    AMFI_API_URL = "https://www.amfiindia.com"
    AMFI_LATEST_API = f"{AMFI_API_URL}/api/latest-nav"
    AMFI_SCHEME_LIST_API = f"{AMFI_API_URL}/api/get-nav-history/navs"
    AMFI_HISTORY_API = f"{AMFI_API_URL}/api/nav-history"

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
    def _api_headers():
        return {
            **AMFIService._headers(),
            "Accept": "application/json, text/plain, */*",
            "Referer": "https://www.amfiindia.com/net-asset-value",
        }

    @staticmethod
    def _normalize_name(value):
        return " ".join(str(value or "").lower().split())

    @staticmethod
    def _flatten_latest_api(data):
        records = []

        def add(record, mf_id=None):
            if not isinstance(record, dict):
                return
            code = (
                record.get("schemeId")
                or record.get("Scheme_Code")
                or record.get("scheme_code")
                or record.get("schemeCode")
            )
            name = (
                record.get("schemeName")
                or record.get("Scheme_Name")
                or record.get("scheme_name")
                or record.get("nav_name")
            )
            current_mf_id = (
                record.get("mutualFundId")
                or record.get("MF_ID")
                or record.get("mf_id")
                or mf_id
            )
            if code and name and current_mf_id:
                records.append({
                    "scheme_code": str(code).strip(),
                    "scheme_name": str(name).strip(),
                    "mf_id": str(current_mf_id).strip(),
                })

        def walk(node, mf_id=None):
            if isinstance(node, dict):
                inherited_mf_id = (
                    node.get("mutualFundId")
                    or node.get("MF_ID")
                    or node.get("mf_id")
                    or mf_id
                )
                add(node, inherited_mf_id)
                for key, value in node.items():
                    walk(
                        value,
                        value if key in {"mutualFundId", "MF_ID", "mf_id"}
                        else inherited_mf_id,
                    )
            elif isinstance(node, list):
                for item in node:
                    walk(item, mf_id)

        walk(data)
        deduped = {}
        for record in records:
            deduped.setdefault(record["scheme_code"], record)
        return deduped

    @staticmethod
    def _resolve_nav_ids(scheme_codes):
        codes = {str(code).strip() for code in scheme_codes}
        if not codes:
            return {}

        response = requests.get(
            AMFIService.AMFI_LATEST_API,
            params={"mfid": "all", "type": ""},
            headers=AMFIService._api_headers(),
            timeout=60,
        )
        response.raise_for_status()
        latest_by_code = AMFIService._flatten_latest_api(response.json())

        requested = {
            code: latest_by_code[code]
            for code in codes
            if code in latest_by_code
        }
        if len(requested) != len(codes):
            missing = sorted(codes - requested.keys())
            raise RuntimeError(
                "AMFI current API did not resolve scheme codes: "
                + ", ".join(missing)
            )

        scheme_lists = {}
        for mf_id in {item["mf_id"] for item in requested.values()}:
            response = requests.get(
                AMFIService.AMFI_SCHEME_LIST_API,
                params={"mf_id": mf_id},
                headers=AMFIService._api_headers(),
                timeout=60,
            )
            response.raise_for_status()
            payload = response.json()
            scheme_lists[mf_id] = (
                payload.get("data", payload)
                if isinstance(payload, dict)
                else payload
            )

        result = {}
        for code, metadata in requested.items():
            target = AMFIService._normalize_name(metadata["scheme_name"])
            candidates = [
                item for item in scheme_lists[metadata["mf_id"]]
                if isinstance(item, dict)
            ]
            matches = [
                item for item in candidates
                if AMFIService._normalize_name(
                    item.get("nav_name") or item.get("scheme_name")
                ) == target
            ]
            if not matches:
                matches = [
                    item for item in candidates
                    if target and target in AMFIService._normalize_name(
                        item.get("nav_name") or item.get("scheme_name")
                    )
                ]
            if not matches:
                raise RuntimeError(
                    f"AMFI current API did not resolve nav_id for scheme {code}."
                )

            if len(matches) > 1:
                matches.sort(
                    key=lambda item: (
                        int(
                            "direct" in target
                            and "direct" in AMFIService._normalize_name(
                                item.get("nav_name") or item.get("scheme_name")
                            )
                        ),
                        int(
                            "growth" in target
                            and "growth" in AMFIService._normalize_name(
                                item.get("nav_name") or item.get("scheme_name")
                            )
                        ),
                        -abs(
                            len(
                                AMFIService._normalize_name(
                                    item.get("nav_name") or item.get("scheme_name")
                                )
                            )
                            - len(target)
                        ),
                    ),
                    reverse=True,
                )

            nav_id = matches[0].get("nav_id")
            if not nav_id:
                raise RuntimeError(
                    f"AMFI current API returned no nav_id for scheme {code}."
                )
            result[code] = {
                "nav_id": str(nav_id),
                "scheme_name": metadata["scheme_name"],
            }

        return result

    @staticmethod
    def _download_historical_api_records(from_date, to_date, scheme_codes):
        if from_date > to_date:
            raise ValueError("From date cannot be after to date.")

        nav_ids = AMFIService._resolve_nav_ids(scheme_codes)
        records = []

        for scheme_code, metadata in nav_ids.items():
            response = requests.get(
                AMFIService.AMFI_HISTORY_API,
                params={
                    "query_type": "historical_period",
                    "sd_id": metadata["nav_id"],
                    "from_date": from_date.strftime("%Y-%m-%d"),
                    "to_date": to_date.strftime("%Y-%m-%d"),
                },
                headers=AMFIService._api_headers(),
                timeout=60,
            )
            response.raise_for_status()
            payload = response.json()
            data = payload.get("data", payload) if isinstance(payload, dict) else payload
            groups = data.get("nav_groups", []) if isinstance(data, dict) else []

            if not groups:
                logger.warning(
                    "AMFI historical API returned no nav_groups for scheme %s",
                    scheme_code,
                )
                continue

            target = AMFIService._normalize_name(metadata["scheme_name"])
            groups = [
                group for group in groups
                if target == AMFIService._normalize_name(group.get("nav_name"))
            ] or groups[:1]

            for group in groups:
                for item in group.get("historical_records", []):
                    try:
                        nav = Decimal(str(item.get("nav")))
                        nav_date = datetime.strptime(
                            str(item.get("date")),
                            "%Y-%m-%d",
                        ).date()
                    except (InvalidOperation, ValueError, TypeError):
                        continue
                    if nav < 0:
                        continue
                    records.append(
                        AMFIService._build_record(
                            scheme_code=scheme_code,
                            isin_first=None,
                            isin_second=None,
                            scheme_name=metadata["scheme_name"],
                            nav=nav,
                            nav_date=nav_date,
                        )
                    )

        return records

    @staticmethod
    def download_historical_nav(
        from_date,
        to_date,
    ):
        """
        Download the real AMFI historical NAV report.

        AMFI documents a maximum 90-day range. The historical download
        endpoint is a text report even though AMFI can return an HTML
        WebForms page for an unsuccessful request. Treat a successful HTTP
        status as insufficient: only a response containing the AMFI
        historical header is accepted as report data.
        """
        if from_date > to_date:
            raise ValueError("From date cannot be after to_date.")

        if (to_date - from_date).days > 90:
            raise ValueError(
                "AMFI historical NAV download supports a maximum period "
                "of 90 days at a time."
            )

        date_params = {
            "frmdt": from_date.strftime("%d-%b-%Y"),
            "todt": to_date.strftime("%d-%b-%Y"),
        }
        headers = {
            **AMFIService._headers(),
            "Accept": "text/plain,text/csv,text/*;q=0.9,*/*;q=0.8",
            "Referer": "https://www.amfiindia.com/net-asset-value/nav-download",
        }

        # AMFI's all-schemes historical download is addressable with
        # mf=0. Keep tp=1 for the text-report mode. A second attempt without
        # tp handles deployments where the flag is inferred by the endpoint.
        attempts = (
            {"mf": "0", "tp": "1", **date_params},
            {"mf": "0", **date_params},
        )
        last_response = None

        with requests.Session() as session:
            for params in attempts:
                response = session.get(
                    AMFIService.NAV_HISTORY_URL,
                    params=params,
                    headers=headers,
                    timeout=60,
                )
                last_response = response
                text = response.text or ""

                # Do not accept an HTTP 200 WebForms/error page as if it
                # were the downloadable report. This was the reason the
                # previous importer could silently return zero records.
                if response.ok and AMFIService._is_historical_report(text):
                    return text

                preview = " ".join(text.split())[:240]
                logger.warning(
                    "AMFI historical response was not a NAV report: "
                    "endpoint=%s status=%s content_type=%s "
                    "from=%s to=%s bytes=%s preview=%r",
                    response.url,
                    response.status_code,
                    response.headers.get("Content-Type", ""),
                    from_date,
                    to_date,
                    len(response.content),
                    preview,
                )

        if last_response is not None:
            last_response.raise_for_status()
            raise RuntimeError(
                "AMFI historical endpoint returned an unexpected response "
                f"for {from_date} to {to_date} "
                f"(status={last_response.status_code}, "
                f"content_type={last_response.headers.get('Content-Type', '')}, "
                f"bytes={len(last_response.content)})."
            )

        raise RuntimeError("AMFI historical endpoint returned no response.")

    @staticmethod
    def _is_historical_report(text):
        """Return True only for AMFI's semicolon-delimited historical report."""
        if not text:
            return False

        lines = [
            line.strip().replace("\ufeff", "")
            for line in text.splitlines()
            if line.strip()
        ]
        if not lines:
            return False

        header_tokens = {
            token.strip().lower()
            for token in lines[0].split(";")
        }
        required = {
            "scheme code",
            "net asset value",
            "date",
        }
        return required.issubset(header_tokens)

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

        # AMFI has published both the 8-column and newer compact 6-column
        # latest formats. In both formats NAV is the penultimate field and
        # Date is the final field.
        if len(parts) < 6:
            return None

        scheme_code = parts[0]
        isin_first = parts[1]
        isin_second = parts[2]
        scheme_name = ";".join(parts[3:-2]).strip()
        nav_text = parts[-2]
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
    def _parse_historical_record(parts, positions=None):
        """Parse one row from AMFI's current historical text report."""
        if len(parts) < 8:
            return None

        if positions:
            scheme_index = positions.get("scheme_code", 0)
            name_index = positions.get("scheme_name", 1)
            nav_index = positions.get("nav", 4)
            date_index = positions.get("date", len(parts) - 1)
            isin_first_index = positions.get("isin_first", 2)
            isin_second_index = positions.get("isin_second", 3)
        else:
            scheme_index = 0
            name_index = 1
            nav_index = 4
            date_index = len(parts) - 1
            isin_first_index = 2
            isin_second_index = 3

        if max(
            scheme_index,
            name_index,
            nav_index,
            date_index,
            isin_first_index,
            isin_second_index,
        ) >= len(parts):
            return None

        scheme_code = parts[scheme_index]
        scheme_name = parts[name_index]
        if not scheme_code.isdigit() or not scheme_name:
            return None

        try:
            nav = Decimal(parts[nav_index])
        except (InvalidOperation, ValueError, TypeError):
            return None
        if nav < 0:
            return None

        try:
            nav_date = datetime.strptime(
                parts[date_index],
                "%d-%b-%Y",
            ).date()
        except ValueError:
            return None

        isin_first = parts[isin_first_index] or None
        isin_second = parts[isin_second_index] or None

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
        scheme_codes=None,
    ):
        """
        Parse AMFI NAV data.

        Supports both latest and historical formats. scheme_codes filters
        historical rows before the full-universe file is parsed into records.
        """
        records = []
        normalized_codes = (
            {str(code).strip() for code in scheme_codes}
            if scheme_codes
            else None
        )
        historical_positions = None

        for raw_line in text.splitlines():
            line = raw_line.strip().replace("\ufeff", "")
            if not line or ";" not in line:
                continue

            parts = [part.strip() for part in line.split(";")]

            if historical and parts[0].strip().lower() == "scheme code":
                normalized = [part.lower() for part in parts]
                historical_positions = {
                    "scheme_code": normalized.index("scheme code"),
                    "scheme_name": (
                        normalized.index("scheme name")
                        if "scheme name" in normalized
                        else normalized.index("nav name")
                    ),
                    "nav": normalized.index("net asset value"),
                    "date": normalized.index("date"),
                    "isin_first": (
                        normalized.index("isin div payout/isin growth")
                        if "isin div payout/isin growth" in normalized
                        else 2
                    ),
                    "isin_second": (
                        normalized.index("isin div reinvestment")
                        if "isin div reinvestment" in normalized
                        else 3
                    ),
                }
                continue

            if not parts[0].isdigit():
                continue

            if historical and normalized_codes and parts[0] not in normalized_codes:
                continue

            record = (
                AMFIService._parse_historical_record(
                    parts,
                    positions=historical_positions,
                )
                if historical
                else AMFIService._parse_latest_record(parts)
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
        Import one bounded AMFI batch while respecting both family-level
        uniqueness constraints on MutualFundScheme.

        AMFI can occasionally publish multiple rows that normalize to the
        same scheme_name.  The database intentionally allows only one scheme
        with a given name inside a family, so those rows must resolve to the
        existing family scheme instead of attempting a second insert.

        Normal rows are still handled in bulk.  Only name-collision rows use
        individual updates, keeping the common path fast.
        """

        from users.permissions import require_active_family

        family = require_active_family(owner)

        # Collapse duplicate AMFI rows by scheme code first.  The last row
        # wins, matching the previous sequential import behavior.
        records_by_code = {}
        for record in records:
            records_by_code[record["scheme_code"]] = record

        records = list(records_by_code.values())
        scheme_codes = [record["scheme_code"] for record in records]
        scheme_names = [record["scheme_name"] for record in records]

        existing_schemes = {
            scheme.scheme_code: scheme
            for scheme in (
                MutualFundScheme.objects
                .filter(
                    family=family,
                    scheme_code__in=scheme_codes,
                )
            )
        }
        existing_by_name = {
            scheme.scheme_name: scheme
            for scheme in (
                MutualFundScheme.objects
                .filter(
                    family=family,
                    scheme_name__in=scheme_names,
                )
            )
        }

        # Resolve every incoming record to one canonical AMFI code.  If the
        # same scheme name appears under multiple codes in one feed, retain
        # the first code because the family has a unique scheme_name.
        canonical_input_code = {}
        first_code_by_name = {}

        for record in records:
            code = record["scheme_code"]
            name = record["scheme_name"]

            existing_by_name_match = existing_by_name.get(name)
            existing_by_code = existing_schemes.get(code)

            if existing_by_name_match is not None and existing_by_code is None:
                canonical_input_code[code] = existing_by_name_match.scheme_code
            elif name in first_code_by_name:
                canonical_input_code[code] = first_code_by_name[name]
            else:
                first_code_by_name[name] = code
                canonical_input_code[code] = code

        canonical_records = {}
        for record in records:
            canonical_code = canonical_input_code[record["scheme_code"]]
            if canonical_code not in canonical_records:
                canonical_records[canonical_code] = record

        canonical_records = list(canonical_records.values())

        canonical_codes = [
            record["scheme_code"]
            for record in canonical_records
        ]

        # Bulk path for schemes that do not collide with an existing
        # family/name row.  Records already mapped to an existing
        # family/name scheme are excluded from this upsert.
        bulk_records = [
            record
            for record in canonical_records
            if not (
                existing_by_name.get(record["scheme_name"]) is not None
                and existing_schemes.get(record["scheme_code"]) is None
            )
        ]

        if bulk_records:
            bulk_by_code = {}

            for record in bulk_records:
                existing = existing_schemes.get(
                    record["scheme_code"]
                )

                bulk_by_code[record["scheme_code"]] = (
                    MutualFundScheme(
                        owner=owner,
                        family=family,
                        scheme_code=record["scheme_code"],
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
                )

            MutualFundScheme.objects.bulk_create(
                list(bulk_by_code.values()),
                update_conflicts=True,
                unique_fields=["family", "scheme_code"],
                update_fields=[
                    "scheme_name",
                    "isin_growth",
                    "isin_dividend",
                ],
            )

        # Re-fetch the canonical scheme rows.  This also gives us the
        # primary key of rows reused because of the family/name uniqueness rule.
        scheme_ids_by_code = dict(
            MutualFundScheme.objects
            .filter(
                family=family,
                scheme_code__in=canonical_codes,
            )
            .values_list(
                "scheme_code",
                "id",
            )
        )

        # Every original AMFI code points to the canonical scheme code that
        # was actually stored for this family.
        original_to_scheme_id = {}
        for original_code, canonical_code in canonical_input_code.items():
            scheme_id = scheme_ids_by_code.get(canonical_code)
            if scheme_id is not None:
                original_to_scheme_id[original_code] = scheme_id

        navs_by_key = {}

        for record in records:
            scheme_id = original_to_scheme_id.get(
                record["scheme_code"]
            )

            if scheme_id is None:
                continue

            nav_key = (scheme_id, record["date"])
            navs_by_key[nav_key] = MutualFundNAV(
                scheme_id=scheme_id,
                date=record["date"],
                source="AMFI",
                nav=record["nav"],
            )

        if navs_by_key:
            MutualFundNAV.objects.bulk_create(
                list(navs_by_key.values()),
                update_conflicts=True,
                unique_fields=["scheme", "date", "source"],
                update_fields=["nav"],
            )

        return len(records), len(records)

    MASTER_SCHEME_BATCH_SIZE = 250
    MASTER_NAV_BATCH_SIZE = 250

    @staticmethod
    @transaction.atomic
    def _import_master_records(records):
        """Upsert AMFI data into the global master tables.

        A latest-NAV download normally contains one row per scheme, while a
        historical download contains many rows per scheme (one per date).
        Keep one canonical scheme row per scheme code, but retain every
        scheme/date NAV observation in the master NAV table.
        """
        if not records:
            return {"schemes": 0, "nav_records": 0}

        schemes_by_code = {}
        nav_records_by_key = {}

        for record in records:
            scheme_code = record["scheme_code"]

            # Keep the most recent metadata row for each scheme while merging
            # any ISIN values that may only appear on one of the rows.
            existing = schemes_by_code.get(scheme_code)
            if existing is None:
                schemes_by_code[scheme_code] = dict(record)
            else:
                if record.get("isin_growth"):
                    existing["isin_growth"] = record["isin_growth"]
                if record.get("isin_dividend"):
                    existing["isin_dividend"] = record["isin_dividend"]
                existing["scheme_name"] = record["scheme_name"]

            if record["date"] is not None:
                nav_records_by_key[(scheme_code, record["date"])] = record

        scheme_records = list(schemes_by_code.values())
        nav_records = list(nav_records_by_key.values())

        schemes = [
            AMFIMasterScheme(
                scheme_code=record["scheme_code"],
                scheme_name=record["scheme_name"],
                isin_growth=record["isin_growth"],
                isin_dividend=record["isin_dividend"],
                is_active=True,
            )
            for record in scheme_records
        ]

        AMFIMasterScheme.objects.bulk_create(
            schemes,
            batch_size=AMFIService.MASTER_SCHEME_BATCH_SIZE,
            update_conflicts=True,
            unique_fields=["scheme_code"],
            update_fields=[
                "scheme_name",
                "isin_growth",
                "isin_dividend",
                "is_active",
                "updated_at",
            ],
        )

        scheme_ids = dict(
            AMFIMasterScheme.objects
            .filter(
                scheme_code__in=[
                    record["scheme_code"]
                    for record in scheme_records
                ]
            )
            .values_list("scheme_code", "id")
        )

        navs = [
            AMFIMasterNAV(
                scheme_id=scheme_ids[record["scheme_code"]],
                date=record["date"],
                nav=record["nav"],
                source="AMFI",
            )
            for record in nav_records
            if record["scheme_code"] in scheme_ids
        ]

        if navs:
            AMFIMasterNAV.objects.bulk_create(
                navs,
                batch_size=AMFIService.MASTER_NAV_BATCH_SIZE,
                update_conflicts=True,
                unique_fields=["scheme", "date", "source"],
                update_fields=["nav"],
            )

        return {
            "schemes": len(scheme_records),
            "nav_records": len(navs),
        }

    @staticmethod
    def import_latest_master_navs():
        """Download AMFI once and refresh the global master dataset."""
        text = AMFIService.download_latest_nav()
        records = AMFIService.parse_nav_file(text, historical=False)
        return AMFIService._import_master_records(records)

    @staticmethod
    def import_historical_master_navs(
        from_date,
        to_date,
        scheme_codes=None,
    ):
        """Import historical AMFI NAVs into the global master dataset.

        ``scheme_codes`` is an optional write-side filter. AMFI's historical
        endpoint returns the full universe for the requested date range, but
        callers such as the benchmark chart only need one scheme. Filtering
        before the database upsert keeps the shared master authoritative
        without duplicating unrelated historical rows.
        """
        text = AMFIService.download_historical_nav(from_date, to_date)
        records = AMFIService.parse_nav_file(
            text,
            historical=True,
            scheme_codes=scheme_codes,
        )
        return AMFIService._import_master_records(records)

    @staticmethod
    def sync_user_nav_from_master(owner):
        """Materialize the latest shared master NAVs into one family."""
        from users.permissions import require_active_family

        family = require_active_family(owner)
        master_schemes = list(AMFIMasterScheme.objects.filter(is_active=True))
        if not master_schemes:
            return {"schemes": 0, "nav_records": 0}

        codes = [scheme.scheme_code for scheme in master_schemes]
        family_schemes = [
            MutualFundScheme(
                owner=owner,
                family=family,
                scheme_code=master.scheme_code,
                scheme_name=master.scheme_name,
                isin_growth=master.isin_growth,
                isin_dividend=master.isin_dividend,
            )
            for master in master_schemes
        ]

        MutualFundScheme.objects.bulk_create(
            family_schemes,
            batch_size=AMFIService.MASTER_SCHEME_BATCH_SIZE,
            update_conflicts=True,
            unique_fields=["family", "scheme_code"],
            update_fields=["scheme_name", "isin_growth", "isin_dividend"],
        )

        scheme_ids = dict(
            MutualFundScheme.objects
            .filter(family=family, scheme_code__in=codes)
            .values_list("scheme_code", "id")
        )

        latest_nav_id = (
            AMFIMasterNAV.objects
            .filter(scheme_id=OuterRef("scheme_id"))
            .order_by("-date", "-id")
            .values("id")[:1]
        )
        latest_master_navs = (
            AMFIMasterNAV.objects
            .filter(
                scheme__is_active=True,
                id=Subquery(latest_nav_id),
            )
            .select_related("scheme")
        )

        family_navs = [
            MutualFundNAV(
                scheme_id=scheme_ids[master_nav.scheme.scheme_code],
                date=master_nav.date,
                nav=master_nav.nav,
                source="AMFI",
            )
            for master_nav in latest_master_navs
            if master_nav.scheme.scheme_code in scheme_ids
        ]

        if family_navs:
            MutualFundNAV.objects.bulk_create(
                family_navs,
                batch_size=AMFIService.MASTER_NAV_BATCH_SIZE,
                update_conflicts=True,
                unique_fields=["scheme", "date", "source"],
                update_fields=["nav"],
            )

        return {"schemes": len(master_schemes), "nav_records": len(family_navs)}
    
    @staticmethod
    def import_latest_navs(owner):
        """Compatibility wrapper: refresh the shared master, then sync one family."""
        AMFIService.import_latest_master_navs()
        return AMFIService.sync_user_nav_from_master(owner)

    @staticmethod
    def import_historical_navs(owner, from_date, to_date):
        """Compatibility wrapper: import history once into the master, then sync the family."""
        AMFIService.import_historical_master_navs(from_date, to_date)
        return AMFIService.sync_user_nav_from_master(owner)
