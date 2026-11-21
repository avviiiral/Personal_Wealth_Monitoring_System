"""Import official AMFI scheme-wise quarterly Average AUM (AAUM).

Examples:
python manage.py import_amfi_scheme_aaum --quarter-start 2026-07-01 --quarter-label "July - September 2026" --file amfi_aaum_report.html
python manage.py import_amfi_scheme_aaum --quarter-start 2026-07-01 --quarter-label "July - September 2026" --url "https://portal.amfiindia.com/AUMReport_Rpt_Po.aspx?dtAUM=01-Jul-2026&mf=0&qt=July+-+September+2026&rpt=scwise" --timeout 180
"""
import re
import urllib.request
from html.parser import HTMLParser
from pathlib import Path
from decimal import Decimal, InvalidOperation

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from mutual_funds.models import AMFIMasterScheme, AMFISchemeAAUM


def normalise_scheme_name(value):
    value = str(value or "").casefold()
    value = value.replace("&", " and ").replace("+", " ")
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return " ".join(value.split())


def decimal_value(value):
    if value is None:
        return None
    raw = str(value).replace(",", "").replace("₹", "").strip()
    if raw.casefold() in {"", "-", "n/a", "na", "nan"}:
        return None
    try:
        result = Decimal(raw)
    except InvalidOperation:
        return None
    return result if result.is_finite() and result >= 0 else None


class _AMFITableParser(HTMLParser):
    """Extract report rows without relying on pandas' inferred multi-row headers."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows = []
        self._in_row = False
        self._in_cell = False
        self._cell_parts = []
        self._row = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() == "tr":
            self._in_row = True
            self._row = []
        elif tag.lower() in {"td", "th"} and self._in_row:
            self._in_cell = True
            self._cell_parts = []

    def handle_data(self, data):
        if self._in_cell:
            self._cell_parts.append(data)

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in {"td", "th"} and self._in_cell:
            self._row.append(" ".join("".join(self._cell_parts).split()))
            self._in_cell = False
            self._cell_parts = []
        elif tag == "tr" and self._in_row:
            if self._row:
                self.rows.append(self._row)
            self._in_row = False
            self._row = []


def parse_amfi_scheme_rows(payload):
    """Return (scheme name, AAUM in Rs lakh) rows from AMFI's scheme-wise HTML.

    AMFI publishes two numeric columns: AAUM excluding domestic FoFs (but
    including overseas FoFs), and AAUM for domestic FoFs. The report's
    category heading determines which column applies to a scheme.
    """
    if isinstance(payload, bytes):
        html = payload.decode("utf-8", errors="replace")
    else:
        html = str(payload)

    parser = _AMFITableParser()
    parser.feed(html)

    parsed_rows = []
    current_category = ""
    for cells in parser.rows:
        if not cells:
            continue

        # Category headings are single-cell rows spanning the report table.
        if len(cells) == 1:
            heading = cells[0].strip()
            if heading and "scheme" in heading.casefold():
                current_category = heading
            elif heading and any(term in heading.casefold() for term in (
                "equity", "debt", "hybrid", "fund of funds", "etf",
                "income", "solution oriented", "other schemes", "index funds",
            )):
                current_category = heading
            continue

        # Scheme rows have a name followed by the two Rs-lakh AAUM values.
        if len(cells) < 3:
            continue
        source_name = cells[0].strip()
        if not source_name or source_name.casefold() in {
            "scheme nav name", "mutual fund total", "grand total",
        }:
            continue

        first_amount = decimal_value(cells[-2])
        domestic_fof_amount = decimal_value(cells[-1])
        if first_amount is None and domestic_fof_amount is None:
            continue

        is_domestic_fof = "fund of funds scheme (domestic)" in current_category.casefold()
        amount_lakh = domestic_fof_amount if is_domestic_fof else first_amount

        # Be defensive if an AMFI row is placed under an unexpected heading.
        # The two columns are mutually exclusive for the scheme rows in the
        # official report; use the populated column rather than importing zero.
        if amount_lakh is None or (amount_lakh == 0 and (
            (is_domestic_fof and first_amount and first_amount > 0)
            or (not is_domestic_fof and domestic_fof_amount and domestic_fof_amount > 0)
        )):
            other_amount = first_amount if is_domestic_fof else domestic_fof_amount
            if other_amount is not None:
                amount_lakh = other_amount

        if amount_lakh is not None:
            parsed_rows.append((source_name, amount_lakh))

    return parsed_rows


class Command(BaseCommand):
    help = "Import official AMFI scheme-wise quarterly Average AUM (AAUM) into local storage."

    def add_arguments(self, parser):
        source = parser.add_mutually_exclusive_group(required=True)
        source.add_argument("--url", help="Official AMFI scheme-wise AAUM report URL.")
        source.add_argument("--file", help="Path to a previously downloaded AMFI HTML report.")
        parser.add_argument("--quarter-start", required=True, help="Quarter start date, YYYY-MM-DD.")
        parser.add_argument("--quarter-label", required=True, help='Display label, e.g. "July - September 2026".')
        parser.add_argument("--timeout", type=int, default=180, help="HTTP timeout in seconds when using --url (default: 180).")
        parser.add_argument("--dry-run", action="store_true", help="Parse and match the report without writing database records.")

    def handle(self, *args, **options):
        try:
            from datetime import date
            quarter_start = date.fromisoformat(options["quarter_start"])
        except ValueError as exc:
            raise CommandError("--quarter-start must use YYYY-MM-DD.") from exc

        if options["file"]:
            report_path = Path(options["file"]).expanduser()
            if not report_path.is_absolute():
                report_path = Path.cwd() / report_path
            try:
                payload = report_path.read_bytes()
            except OSError as exc:
                raise CommandError(f"Unable to read AMFI report file '{report_path}': {exc}") from exc
            self.stdout.write(f"Reading AMFI report from local file: {report_path}")
        else:
            request = urllib.request.Request(
                options["url"],
                headers={"User-Agent": "PWMS/1.0 (AMFI public disclosure importer)", "Accept": "text/html,*/*"},
            )
            try:
                with urllib.request.urlopen(request, timeout=options["timeout"]) as response:
                    payload = response.read()
            except Exception as exc:
                raise CommandError(f"Unable to download the AMFI report: {exc}") from exc

        rows = parse_amfi_scheme_rows(payload)
        if not rows:
            raise CommandError(
                "No scheme-level AAUM rows were found in the AMFI report. "
                "No records were written; verify that the input is the scheme-wise AAUM HTML report."
            )

        masters = list(AMFIMasterScheme.objects.filter(is_active=True, sebi_universe_active=True))
        exact_map = {}
        for scheme in masters:
            exact_map.setdefault(normalise_scheme_name(scheme.scheme_name), []).append(scheme)

        parsed = {}
        unmatched = 0
        for source_name, amount_lakh in rows:
            matches = exact_map.get(normalise_scheme_name(source_name), [])
            if len(matches) != 1:
                unmatched += 1
                continue
            scheme = matches[0]
            # AMFI publishes Rs. Lakhs. Store crore (100 lakh = 1 crore).
            parsed[scheme.pk] = (scheme, amount_lakh / Decimal("100"), source_name)

        if not parsed:
            raise CommandError(
                f"Parsed {len(rows)} scheme rows, but no unambiguous names matched "
                "the local SEBI universe. No records were written."
            )

        if options["dry_run"]:
            self.stdout.write(self.style.SUCCESS(
                f"DRY RUN: parsed {len(rows)} scheme rows; matched {len(parsed)} records; "
                f"unmatched or ambiguous rows: {unmatched}. No database records were written."
            ))
            for scheme, amount_cr, source_name in list(parsed.values())[:10]:
                self.stdout.write(f"  {scheme.scheme_name}: ₹{amount_cr:,.2f} Cr")
            return

        with transaction.atomic():
            for scheme, amount_cr, source_name in parsed.values():
                AMFISchemeAAUM.objects.update_or_create(
                    scheme=scheme,
                    quarter_start=quarter_start,
                    source="AMFI_SCHEME_AAUM",
                    defaults={
                        "quarter_label": options["quarter_label"],
                        "average_aum_cr": amount_cr,
                        "source_scheme_name": source_name,
                    },
                )

        self.stdout.write(self.style.SUCCESS(
            f"Parsed {len(rows)} AMFI scheme rows; imported {len(parsed)} matched AAUM records "
            f"for {options['quarter_label']}. Unmatched or ambiguous rows: {unmatched}. "
            "Values stored in ₹ crore. No IndianAPI requests used."
        ))
