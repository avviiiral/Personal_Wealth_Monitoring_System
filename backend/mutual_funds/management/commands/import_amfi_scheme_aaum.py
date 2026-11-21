"""Import official AMFI scheme-wise quarterly Average AUM (AAUM).

Example:
python manage.py import_amfi_scheme_aaum --quarter-start 2026-07-01 --quarter-label "July - September 2026" --url "https://portal.amfiindia.com/AUMReport_Rpt_Po.aspx?dtAUM=01-Jul-2026&mf=0&qt=July+-+September+2026&rpt=scwise"
"""
import re
import urllib.request
from decimal import Decimal, InvalidOperation

import pandas as pd
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from mutual_funds.models import AMFIMasterScheme, AMFISchemeAAUM


def normalise_scheme_name(value):
    value = str(value or "").casefold()
    value = value.replace("&", " and ").replace("+", " ")
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return " ".join(value.split())


def decimal_value(value):
    if value is None or pd.isna(value):
        return None
    raw = str(value).replace(",", "").replace("₹", "").strip()
    if raw in {"", "-", "N/A", "na"}:
        return None
    try:
        result = Decimal(raw)
    except InvalidOperation:
        return None
    return result if result.is_finite() and result >= 0 else None


class Command(BaseCommand):
    help = "Import official AMFI scheme-wise quarterly Average AUM (AAUM) into local storage."

    def add_arguments(self, parser):
        parser.add_argument("--url", required=True, help="Official AMFI scheme-wise AAUM report URL.")
        parser.add_argument("--quarter-start", required=True, help="Quarter start date, YYYY-MM-DD.")
        parser.add_argument("--quarter-label", required=True, help='Display label, e.g. "July - September 2026".')
        parser.add_argument("--timeout", type=int, default=45)

    def handle(self, *args, **options):
        try:
            from datetime import date
            quarter_start = date.fromisoformat(options["quarter_start"])
        except ValueError as exc:
            raise CommandError("--quarter-start must use YYYY-MM-DD.") from exc

        request = urllib.request.Request(
            options["url"],
            headers={"User-Agent": "PWMS/1.0 (AMFI public disclosure importer)", "Accept": "text/html,*/*"},
        )
        try:
            with urllib.request.urlopen(request, timeout=options["timeout"]) as response:
                payload = response.read()
        except Exception as exc:
            raise CommandError(f"Unable to download the AMFI report: {exc}") from exc

        try:
            tables = pd.read_html(payload)
        except Exception as exc:
            raise CommandError(f"Unable to parse AMFI report tables: {exc}") from exc
        if not tables:
            raise CommandError("AMFI report did not contain any tabular data.")

        masters = list(AMFIMasterScheme.objects.filter(is_active=True, sebi_universe_active=True))
        exact_map = {}
        for scheme in masters:
            exact_map.setdefault(normalise_scheme_name(scheme.scheme_name), []).append(scheme)

        parsed = {}
        unmatched = 0
        for frame in tables:
            frame.columns = [str(column).strip() for column in frame.columns]
            name_col = next((column for column in frame.columns if "scheme" in column.casefold() and "name" in column.casefold()), None)
            if name_col is None:
                name_col = next((column for column in frame.columns if "scheme" in column.casefold() and "nav" in column.casefold()), None)
            aum_col = next((column for column in frame.columns if "average aum" in column.casefold()), None)
            if name_col is None or aum_col is None:
                continue
            for _, row in frame.iterrows():
                source_name = str(row.get(name_col, "")).strip()
                if not source_name or source_name.casefold() in {"nan", "mutual fund total"}:
                    continue
                amount_lakh = decimal_value(row.get(aum_col))
                if amount_lakh is None:
                    continue
                matches = exact_map.get(normalise_scheme_name(source_name), [])
                if len(matches) != 1:
                    unmatched += 1
                    continue
                scheme = matches[0]
                # AMFI publishes this report in Rs. Lakhs. Store crore (100 lakh = 1 crore).
                parsed[scheme.pk] = (scheme, amount_lakh / Decimal("100"), source_name)

        if not parsed:
            raise CommandError(
                "No unambiguous scheme names matched the local SEBI universe. "
                "No records were written; inspect the report columns and AMFI scheme-name variants."
            )

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
            f"Imported {len(parsed)} scheme AAUM records for {options['quarter_label']}. "
            f"Unmatched or ambiguous rows: {unmatched}. "
            f"Source: AMFI scheme-wise Average AUM. Values stored in ₹ crore."
        ))
