from datetime import date, datetime, timedelta
from decimal import Decimal

import requests

from django.core.management.base import (
    BaseCommand,
    CommandError,
)

from mutual_funds.models import (
    MutualFundNAV,
    MutualFundScheme,
)


AMFI_HISTORY_URL = (
    "https://portal.amfiindia.com/"
    "DownloadNAVHistoryReport_Po.aspx"
)

AMC_CODES = {
    "360_ONE": 62,
}


class Command(BaseCommand):

    help = (
        "Import historical mutual fund NAV "
        "data from AMFI."
    )

    def add_arguments(self, parser):

        parser.add_argument(
            "--scheme-code",
            required=True,
            type=str,
        )

        parser.add_argument(
            "--from-date",
            default="2026-01-01",
            type=str,
        )

        parser.add_argument(
            "--to-date",
            default="2026-08-07",
            type=str,
        )

    def handle(self, *args, **options):

        scheme_code = options["scheme_code"]

        try:
            start_date = date.fromisoformat(
                options["from_date"]
            )

            end_date = date.fromisoformat(
                options["to_date"]
            )

        except ValueError:

            raise CommandError(
                "Dates must use YYYY-MM-DD format."
            )

        if start_date > end_date:

            raise CommandError(
                "from-date cannot be after to-date."
            )

        try:

            scheme = (
                MutualFundScheme.objects
                .get(
                    scheme_code=scheme_code
                )
            )

        except MutualFundScheme.DoesNotExist:

            raise CommandError(
                f"Scheme with code "
                f"{scheme_code} does not exist."
            )

        self.stdout.write(
            self.style.SUCCESS(
                f"Scheme found: "
                f"{scheme.scheme_name}"
            )
        )

        self.stdout.write(
            f"Import range: "
            f"{start_date} → {end_date}"
        )

        amc_code = AMC_CODES["360_ONE"]

        self.stdout.write(
            f"Using AMFI AMC code: {amc_code}"
        )

        total_created = 0
        total_updated = 0
        total_skipped = 0

        current_start = start_date

        while current_start <= end_date:

            current_end = min(
                current_start + timedelta(days=89),
                end_date,
            )

            self.stdout.write("")

            self.stdout.write(
                f"Downloading AMFI NAV history: "
                f"{current_start} → {current_end}"
            )

            rows = self.fetch_period(
                amc_code=amc_code,
                start_date=current_start,
                end_date=current_end,
            )

            self.stdout.write(
                f"Rows received: {len(rows)}"
            )

            matched_rows = [
                row
                for row in rows
                if row["scheme_code"] == scheme_code
            ]

            self.stdout.write(
                f"Rows for scheme {scheme_code}: "
                f"{len(matched_rows)}"
            )

            for row in matched_rows:

                nav_date = row["date"]
                nav_value = row["nav"]

                if nav_value <= 0:

                    total_skipped += 1
                    continue

                obj, created = (
                    MutualFundNAV.objects
                    .update_or_create(
                        scheme=scheme,
                        date=nav_date,
                        defaults={
                            "nav": nav_value,
                            "source": "AMFI",
                        },
                    )
                )

                if created:

                    total_created += 1

                else:

                    total_updated += 1

            current_start = (
                current_end
                + timedelta(days=1)
            )

        total_records = (
            MutualFundNAV.objects
            .filter(
                scheme=scheme
            )
            .count()
        )

        self.stdout.write("")

        self.stdout.write(
            self.style.SUCCESS(
                "Historical NAV import completed."
            )
        )

        self.stdout.write(
            f"Created: {total_created}"
        )

        self.stdout.write(
            f"Updated: {total_updated}"
        )

        self.stdout.write(
            f"Skipped: {total_skipped}"
        )

        self.stdout.write(
            f"Total NAV records for scheme: "
            f"{total_records}"
        )

    def fetch_period(
        self,
        amc_code,
        start_date,
        end_date,
    ):

        params = {
            "mf": str(amc_code),
            "tp": "1",
            "frmdt": start_date.strftime(
                "%d-%b-%Y"
            ),
            "todt": end_date.strftime(
                "%d-%b-%Y"
            ),
        }

        response = requests.get(
            AMFI_HISTORY_URL,
            params=params,
            timeout=60,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 "
                    "PWMS Mutual Fund NAV Importer"
                ),
                "Accept": (
                    "text/plain,text/html,"
                    "application/xhtml+xml,"
                    "application/xml;q=0.9,"
                    "*/*;q=0.8"
                ),
            },
        )

        response.raise_for_status()

        return self.parse_response(
            response.text
        )

    def parse_response(
        self,
        content,
    ):

        rows = []

        for line in content.splitlines():

            line = line.strip()

            if not line:
                continue

            if ";" not in line:
                continue

            parts = [
                part.strip()
                for part in line.split(";")
            ]

            # Expected AMFI historical format:
            #
            # 0 Scheme Code
            # 1 Scheme Name
            # 2 ISIN Div Payout / Growth
            # 3 ISIN Div Reinvestment
            # 4 NAV
            # 5 Repurchase Price
            # 6 Sale Price
            # 7 Date

            if len(parts) < 8:
                continue

            scheme_code = parts[0]

            if not scheme_code.isdigit():
                continue

            nav_raw = parts[4]
            date_raw = parts[7]

            try:

                nav_value = Decimal(
                    nav_raw.replace(
                        ",",
                        ""
                    )
                )

            except Exception:

                continue

            try:

                nav_date = self.parse_date(
                    date_raw
                )

            except ValueError:

                continue

            rows.append(
                {
                    "scheme_code": scheme_code,
                    "nav": nav_value,
                    "date": nav_date,
                }
            )

        return rows

    @staticmethod
    def parse_date(value):

        formats = (
            "%d-%b-%Y",
            "%d-%b-%y",
            "%d/%m/%Y",
            "%d/%m/%y",
        )

        value = value.strip()

        for fmt in formats:

            try:

                return datetime.strptime(
                    value,
                    fmt,
                ).date()

            except ValueError:

                continue

        raise ValueError(
            f"Unsupported AMFI date: {value}"
        )