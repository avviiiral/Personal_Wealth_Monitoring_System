from io import StringIO
from urllib.parse import urljoin

import pandas as pd
from django.db import transaction

from .underlying import MutualFundUnderlyingService
from mutual_funds.models import MutualFundUnderlying


class OfficialMutualFundUnderlyingService(MutualFundUnderlyingService):
    """AMFI/AMC importer that also preserves the disclosed industry/sector."""

    # Official AMC disclosure landing pages are fallbacks only. The importer
    # still validates the requested scheme by scheme code, ISIN or name before
    # accepting a document. These pages are not fund-specific.
    OFFICIAL_AMC_PAGES = {
        "bandhan": (
            "https://cmsnew.bandhanmutual.com/category/scheme-portfolios/page/3/",
            "https://bandhanmutual.com/statutory-disclosures/scheme-portfolios/fortnightly",
        ),
        "hdfc": (
            "https://www.hdfcfund.com/statutory-disclosure/portfolio/fortnightly-portfolio",
            "https://www.hdfcfund.com/statutory-disclosure/portfolio/monthly-portfolio",
        ),
        "icici": (
            "https://www.icicipruamc.com/news-and-media/downloads?currentTabFilter=Other+SchemeDisclosures&subCatTabFilter=Monthly%20Portfolio%20Disclosures",
        ),
        "kotak": (
            "https://www.kotakmf.com/Information/statutory-disclosure",
        ),
    }

    @classmethod
    def _parse_dataframe(cls, dataframe, fallback_date=None):
        dataframe = dataframe.dropna(how="all").copy()

        if dataframe.empty:
            return []

        dataframe.columns = [
            cls.normalize_text(column)
            for column in dataframe.columns
        ]

        def find_column(*aliases):
            normalized_columns = {
                column: cls.normalize_column(column)
                for column in dataframe.columns
            }

            for alias in aliases:
                normalized_alias = cls.normalize_column(alias)
                for column, normalized_column in normalized_columns.items():
                    if normalized_column == normalized_alias:
                        return column

            for alias in aliases:
                normalized_alias = cls.normalize_column(alias)
                if not normalized_alias:
                    continue
                for column, normalized_column in normalized_columns.items():
                    if normalized_alias in normalized_column:
                        return column

            return None

        name_col = find_column(
            "security_name",
            "name of the instrument",
            "name of instrument",
            "security name",
            "instrument name",
            "instrument",
        )
        if not name_col:
            return []

        isin_col = find_column("isin", "isin code", "isin no", "isin number")
        quantity_col = find_column(
            "quantity", "qty", "units", "no of shares", "number of shares", "shares"
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
        sector_col = find_column(
            "industry",
            "sector",
        )

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
        """Parse spreadsheet/CSV/HTML using StringIO for pandas HTML support."""
        portfolio_date = cls._extract_date(filename) or fallback_date
        lower_name = filename.lower()
        frames = []

        if lower_name.endswith((".xlsx", ".xls")):
            workbook = pd.ExcelFile(
                content if hasattr(content, "read") else __import__("io").BytesIO(content)
            )
            for sheet in workbook.sheet_names:
                try:
                    frames.append(pd.read_excel(workbook, sheet_name=sheet, header=0))
                except Exception:
                    continue
        elif lower_name.endswith(".csv"):
            frames.append(pd.read_csv(__import__("io").BytesIO(content)))
        else:
            html = (
                content.decode("utf-8", errors="ignore")
                if isinstance(content, bytes)
                else str(content)
            )
            try:
                frames.extend(pd.read_html(StringIO(html)))
            except (ValueError, ImportError):
                frames = []

        records = []
        for frame in frames:
            records.extend(cls._parse_dataframe(frame, portfolio_date))
        return records

    @classmethod
    def _fallback_pages_for_scheme(cls, scheme):
        amc = cls.normalize_text(getattr(scheme, "amc_name", "")).lower()
        pages = []
        for key, urls in cls.OFFICIAL_AMC_PAGES.items():
            if key in amc:
                pages.extend(urls)
        return pages

    @classmethod
    def _collect_official_candidates(cls, scheme):
        candidates = list(cls.discover_documents(scheme))
        for page_url in cls._fallback_pages_for_scheme(scheme):
            try:
                response = cls._fetch(page_url)
            except Exception:
                continue
            html = response.text
            if cls._matches_scheme(html, scheme):
                candidates.append(page_url)
            for child in cls._official_links(html, page_url):
                if cls._matches_scheme(child, scheme):
                    candidates.append(child)
                if child.lower().endswith((".xlsx", ".xls", ".csv")) and cls._matches_scheme(child, scheme):
                    candidates.append(child)
        return list(dict.fromkeys(candidates))

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
        documents = cls._collect_official_candidates(scheme)
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
                result = cls.import_document(
                    scheme,
                    response.content,
                    filename,
                    document_url,
                    fallback_date=fallback_date,
                )
                return result
            except Exception as exc:
                last_error = exc
                continue

        raise ValueError(
            f"All official portfolio disclosures failed for {scheme.scheme_name}: {last_error}"
        )
