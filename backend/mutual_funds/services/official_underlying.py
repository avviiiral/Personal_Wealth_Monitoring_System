from io import BytesIO, StringIO

import pandas as pd
from django.db import transaction

from .underlying import MutualFundUnderlyingService
from mutual_funds.models import MutualFundUnderlying


class OfficialMutualFundUnderlyingService(MutualFundUnderlyingService):
    """Import official mutual-fund portfolio disclosures and preserve sector."""

    @classmethod
    def _parse_dataframe(cls, dataframe, fallback_date=None):
        dataframe = dataframe.dropna(how="all").copy()
        if dataframe.empty:
            return []

        # Flatten MultiIndex headers produced by some AMC HTML tables.
        if isinstance(dataframe.columns, pd.MultiIndex):
            dataframe.columns = [
                " ".join(
                    cls.normalize_text(part)
                    for part in column
                    if cls.normalize_text(part) and cls.normalize_text(part).lower() != "nan"
                )
                for column in dataframe.columns
            ]
        else:
            dataframe.columns = [cls.normalize_text(column) for column in dataframe.columns]

        def find_column(*aliases):
            normalized_columns = {
                column: cls.normalize_column(column)
                for column in dataframe.columns
            }

            normalized_aliases = [cls.normalize_column(alias) for alias in aliases]

            # Exact normalized match.
            for alias in normalized_aliases:
                for column, normalized_column in normalized_columns.items():
                    if normalized_column == alias:
                        return column

            # Partial match for AMC variants such as
            # "Market Value(Rs.in Lakhs)".
            for alias in normalized_aliases:
                if not alias:
                    continue
                for column, normalized_column in normalized_columns.items():
                    if alias in normalized_column:
                        return column

            return None

        name_col = find_column(
            "security_name",
            "name of the instrument",
            "name of instrument",
            "security name",
            "instrument name",
            "instrument",
            "scrip name",
        )
        if not name_col:
            return []

        isin_col = find_column("isin", "isin code", "isin no", "isin number")
        quantity_col = find_column(
            "quantity",
            "qty",
            "units",
            "no of shares",
            "number of shares",
            "shares",
        )
        value_col = find_column(
            "market_value",
            "market value",
            "market value (rs.)",
            "market value (in rs.)",
            "market value rs",
            "fair value",
            "valuation",
            "value",
        )
        pct_col = find_column(
            "percentage_of_nav",
            "% to nav",
            "% of nav",
            "percent of nav",
            "percentage of nav",
            "% nav",
            "nav (%)",
            "weight",
            "weight (%)",
            "portfolio (%)",
            "percentage",
        )
        sector_col = find_column("industry", "sector")

        value_is_lakhs = False
        if value_col:
            normalized_value_column = cls.normalize_column(value_col)
            value_is_lakhs = any(
                token in normalized_value_column
                for token in ("lakh", "lakhs", "lac", "lacs")
            )

        rows = []
        for _, row in dataframe.iterrows():
            security_name = cls.normalize_text(row.get(name_col))
            if not security_name:
                continue

            lower_name = security_name.lower().strip()
            if (
                lower_name in {
                    "total",
                    "grand total",
                    "total investments",
                    "net investments",
                }
                or lower_name.startswith("total ")
                or security_name.isdigit()
            ):
                continue

            isin = cls.normalize_text(row.get(isin_col)) if isin_col else None
            quantity = cls._decimal(row.get(quantity_col)) if quantity_col else None
            market_value = cls._decimal(row.get(value_col)) if value_col else None
            if market_value is not None and value_is_lakhs:
                market_value *= 100000

            percentage = cls._decimal(row.get(pct_col)) if pct_col else None
            if percentage is None or percentage < 0 or percentage > 100:
                continue

            sector = cls.normalize_text(row.get(sector_col)) if sector_col else None
            if sector in {"-", "N.A.", "NA", "N/A", ""}:
                sector = None

            rows.append(
                {
                    "security_name": security_name,
                    "isin": isin or None,
                    "security_key": cls.normalize_security_key(security_name, isin),
                    "quantity": quantity,
                    "market_value": market_value,
                    "percentage_of_nav": percentage,
                    "sector": sector,
                    "portfolio_date": fallback_date,
                }
            )

        return rows

    @classmethod
    def parse_document(cls, content, filename, fallback_date=None):
        """Parse Excel, CSV, or HTML portfolio disclosures."""
        portfolio_date = cls._extract_date(filename) or fallback_date
        lower_name = filename.lower()
        frames = []

        if lower_name.endswith((".xlsx", ".xls")):
            workbook = pd.ExcelFile(
                content if hasattr(content, "read") else BytesIO(content)
            )
            for sheet in workbook.sheet_names:
                try:
                    frames.append(pd.read_excel(workbook, sheet_name=sheet, header=0))
                except Exception:
                    continue

        elif lower_name.endswith(".csv"):
            frames.append(pd.read_csv(BytesIO(content)))

        else:
            html = content.decode("utf-8", errors="ignore") if isinstance(content, bytes) else str(content)

            # Explicitly provide header=0. This avoids parser-dependent header
            # inference on AMC HTML tables.
            for header in (0, None):
                try:
                    parsed = pd.read_html(StringIO(html), header=header)
                except (ValueError, ImportError):
                    continue
                frames.extend(parsed)
                if parsed:
                    break

        records = []
        for frame in frames:
            records.extend(cls._parse_dataframe(frame, portfolio_date))

        return records

    @classmethod
    @transaction.atomic
    def import_document(cls, scheme, content, filename, source_reference, fallback_date=None):
        records = cls.parse_document(content, filename, fallback_date=fallback_date)
        if not records:
            raise ValueError(f"No valid portfolio rows found in {filename}.")

        portfolio_date = next(
            (row["portfolio_date"] for row in records if row["portfolio_date"]),
            None,
        )
        if portfolio_date is None:
            raise ValueError(f"Could not determine portfolio date for {filename}.")

        existing_count = MutualFundUnderlying.objects.filter(
            scheme=scheme,
            portfolio_date=portfolio_date,
            source=cls.SOURCE,
        ).count()
        if existing_count:
            return {
                "status": "already_imported",
                "portfolio_date": portfolio_date,
                "records": existing_count,
            }

        objects = [
            MutualFundUnderlying(
                scheme=scheme,
                security_name=row["security_name"],
                isin=row["isin"],
                security_key=row["security_key"],
                quantity=row["quantity"],
                market_value=row["market_value"],
                percentage_of_nav=row["percentage_of_nav"],
                sector=row.get("sector"),
                portfolio_date=portfolio_date,
                source=cls.SOURCE,
                source_reference=source_reference,
            )
            for row in records
        ]
        MutualFundUnderlying.objects.bulk_create(
            objects,
            ignore_conflicts=True,
            batch_size=500,
        )
        return {
            "status": "imported",
            "portfolio_date": portfolio_date,
            "records": len(objects),
        }

    @classmethod
    def fetch_scheme(cls, scheme):
        documents = cls.discover_documents(scheme)
        if not documents:
            raise ValueError(
                f"No official AMFI/AMC portfolio disclosure was found for {scheme.scheme_name} "
                f"(scheme code={scheme.scheme_code}, ISIN={scheme.isin_growth or scheme.isin_dividend})."
            )

        last_error = None
        for document_url in documents:
            try:
                response = cls._fetch(document_url)
                path_name = document_url.rstrip("/").rsplit("/", 1)[-1]
                filename = path_name if "." in path_name else "portfolio.html"
                fallback_date = cls._extract_date(response.text)
                return cls.import_document(
                    scheme,
                    response.content,
                    filename,
                    document_url,
                    fallback_date=fallback_date,
                )
            except Exception as exc:
                last_error = exc

        raise ValueError(
            f"All official portfolio disclosures failed for {scheme.scheme_name}: {last_error}"
        )
