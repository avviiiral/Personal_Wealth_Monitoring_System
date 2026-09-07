"""
Classify stocks into Large/Mid/Small Cap using AMFI's actual
published methodology - a RANK within the market (top 100 = Large,
101-250 = Mid, the rest = Small), per SEBI circular
SEBI/HO/IMD/DF3/CIR/P/2017/114 - not a fixed rupee threshold, which
drifts every period and would need updating by hand forever.

Source data: AMFI publishes this list twice a year at
amfiindia.com/otherdata/categorisation-of-stocks. Unlike NAV data,
there's no stable, predictable URL to fetch this from automatically
- the file naming has changed formats across periods (recent ones
are .xlsx, e.g. "AverageMarketCapitalization30Jun2025.pdf" for some
periods, "Average Market Capitalization of listed companies during
Jan-June 2021.xlsx" for others) - the same class of limitation as
the AMC portfolio disclosure files (see
mutual_funds/services/mutual_fund_holdings.py). So this command
takes a manually-downloaded file rather than pretending to fetch
one reliably; the ranking/classification itself is what's fully
automated, so re-running this each period needs no manual bucketing
- just the download.

Column headers are matched by synonym (not fixed position), same
approach as the AMC disclosure parser - this has NOT been verified
against a real downloaded AMFI file, since fetching one requires a
browser session I don't have in this environment. Expect to adjust
COLUMN_SYNONYMS the first time this runs against a real file.
"""

from decimal import Decimal, InvalidOperation

import pandas as pd

from django.contrib.auth.models import User
from django.core.management.base import (
    BaseCommand,
    CommandError,
)

from investments.models import SecurityMaster

LARGE_CAP_RANK_CUTOFF = 100
MID_CAP_RANK_CUTOFF = 250

COLUMN_SYNONYMS = {
    "isin": ["isin"],
    "company_name": ["company name", "name of company", "company"],
    "market_cap_bse": [
        "bse 6 month avg total market cap in (rs. crs.)",
        "bse 6 month avg total market cap in (rs cr)",
        "bse average market capitalization",
    ],
    "market_cap_nse": [
        "nse 6 month avg total market cap in (rs. crs.)",
        "nse 6 month avg total market cap in (rs cr)",
        "nse average market capitalization",
    ],
}


def _normalize_header(value):
    if value is None:
        return ""
    return " ".join(str(value).strip().lower().split())


def _find_header_row_and_columns(raw_rows):
    for row_index, row in enumerate(raw_rows[:20]):

        column_map = {}

        for col_index, cell in enumerate(row):
            normalized = _normalize_header(cell)

            if not normalized:
                continue

            for field, variants in COLUMN_SYNONYMS.items():
                if normalized in variants and field not in column_map:
                    column_map[field] = col_index

        if "isin" in column_map and (
            "market_cap_bse" in column_map
            or "market_cap_nse" in column_map
        ):
            return row_index, column_map

    raise CommandError(
        "Could not locate a recognizable header row (needs an "
        "ISIN column and at least one market-cap column) in the "
        "first 20 rows of the file."
    )


def _cell_text(row, column_map, field):
    col_index = column_map.get(field)

    if col_index is None or col_index >= len(row):
        return ""

    value = row[col_index]

    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""

    return str(value).strip()


def _cell_decimal(row, column_map, field):
    text = _cell_text(row, column_map, field)

    if not text:
        return None

    cleaned = text.replace(",", "").strip()

    if not cleaned or cleaned in ("-", "NA", "N/A"):
        return None

    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None


class Command(BaseCommand):

    help = (
        "Classify stocks Large/Mid/Small Cap from an AMFI average "
        "market capitalization file, by rank (top 100/101-250/"
        "rest), matched to SecurityMaster by ISIN. Dry-run by "
        "default; pass --apply to write."
    )

    def add_arguments(self, parser):

        parser.add_argument(
            "--file",
            type=str,
            required=True,
            help="Path to the downloaded AMFI market cap .xlsx file.",
        )

        parser.add_argument(
            "--user-id",
            type=int,
            default=None,
            help=(
                "Restrict to one user's SecurityMaster records. "
                "Omit to cover every user."
            ),
        )

        parser.add_argument(
            "--apply",
            action="store_true",
            help="Actually save. Without this flag, only prints a report.",
        )

        parser.add_argument(
            "--overwrite",
            action="store_true",
            help=(
                "Overwrite an existing cap_type value too. Without "
                "this flag, only null cap_type fields are filled in."
            ),
        )

    def handle(self, *args, **options):

        file_path = options["file"]
        user_id = options.get("user_id")
        apply_changes = options["apply"]
        overwrite = options["overwrite"]

        try:
            dataframe = pd.read_excel(file_path, header=None)
        except Exception as exc:
            raise CommandError(
                f"Unable to read {file_path!r} as an .xlsx file: {exc}"
            )

        raw_rows = dataframe.values.tolist()

        header_row_index, column_map = _find_header_row_and_columns(
            raw_rows
        )

        ranked = []

        for row in raw_rows[header_row_index + 1:]:

            isin = _cell_text(row, column_map, "isin")
            company_name = _cell_text(row, column_map, "company_name")

            if not isin:
                continue

            bse_cap = _cell_decimal(row, column_map, "market_cap_bse")
            nse_cap = _cell_decimal(row, column_map, "market_cap_nse")

            candidates = [c for c in (bse_cap, nse_cap) if c is not None]

            if not candidates:
                continue

            market_cap = max(candidates)

            ranked.append((market_cap, isin, company_name))

        if not ranked:
            raise CommandError(
                "No rows with both an ISIN and a market-cap value "
                "were found - check the file/column headers."
            )

        ranked.sort(key=lambda entry: entry[0], reverse=True)

        classified_by_isin = {}

        for rank, (market_cap, isin, company_name) in enumerate(
            ranked, start=1
        ):

            if rank <= LARGE_CAP_RANK_CUTOFF:
                cap_type = "Large Cap"
            elif rank <= MID_CAP_RANK_CUTOFF:
                cap_type = "Mid Cap"
            else:
                cap_type = "Small Cap"

            classified_by_isin[isin] = (cap_type, rank, company_name)

        securities = SecurityMaster.objects.exclude(
            isin__isnull=True
        ).exclude(isin__exact="")

        if user_id is not None:

            try:
                User.objects.get(id=user_id)
            except User.DoesNotExist:
                raise CommandError(f"User with ID {user_id} does not exist.")

            securities = securities.filter(owner_id=user_id)

        updated_count = 0
        to_save = []

        for security in securities:

            classification = classified_by_isin.get(security.isin)

            if classification is None:
                continue

            cap_type, rank, company_name = classification

            if security.cap_type and not overwrite:
                continue

            if security.cap_type == cap_type:
                continue

            self.stdout.write(
                f"{security.asset_name!r} (ISIN {security.isin}, "
                f"owner #{security.owner_id}) rank {rank}: "
                f"{security.cap_type!r} -> {cap_type!r}"
            )

            security.cap_type = cap_type
            to_save.append(security)
            updated_count += 1

        if not apply_changes:
            self.stdout.write("")
            self.stdout.write(
                f"DRY RUN - would update {updated_count} row(s). "
                "Re-run with --apply to save."
            )
            return

        for security in to_save:
            security.save(update_fields=["cap_type", "updated_at"])

        self.stdout.write("")
        self.stdout.write(f"Updated {updated_count} SecurityMaster row(s).")
