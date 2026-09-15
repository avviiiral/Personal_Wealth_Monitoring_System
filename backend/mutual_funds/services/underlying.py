import io
import logging
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from urllib.parse import urljoin, urlparse

import pandas as pd
import requests
from django.db import transaction
from django.utils import timezone

from mutual_funds.models import MutualFundScheme, MutualFundUnderlying

logger = logging.getLogger(__name__)


class MutualFundUnderlyingService:
    """
    Import monthly/fortnightly mutual-fund portfolio disclosures from
    official AMFI/AMC sources.

    The service deliberately stores the disclosed portfolio date separately
    from fetched_at. A newly fetched copy for an existing portfolio date is
    idempotent; an older portfolio date is never overwritten.
    """

    AMFI_DISCLOSURE_URL = "https://www.amfiindia.com/online-center/portfolio-disclosure"
    SOURCE = "AMFI"
    TIMEOUT_SECONDS = 60

    COLUMN_ALIASES = {
        "security_name": {
            "name", "security", "security name", "instrument", "issuer",
            "company", "company name", "stock", "scheme name", "scrip name",
        },
        "isin": {"isin", "isin code", "isin no", "isin number"},
        "quantity": {
            "quantity", "qty", "units", "no of shares", "number of shares",
            "shares", "face value units",
        },
        "market_value": {
            "market value", "market value (rs.)", "market value (in rs.)",
            "fair value", "valuation", "value", "market/fair value",
        },
        "percentage_of_nav": {
            "% to nav", "% of nav", "percent of nav", "percentage of nav",
            "% nav", "nav (%)", "weight", "weight (%)", "portfolio (%)",
            "percentage",
        },
    }

    @classmethod
    def _headers(cls):
        return {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/153.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }

    @staticmethod
    def normalize_text(value):
        value = "" if value is None else str(value)
        value = value.replace("\u00a0", " ")
        return re.sub(r"\s+", " ", value).strip()

    @classmethod
    def normalize_column(cls, value):
        value = cls.normalize_text(value).lower()
        value = value.replace("%", "percent")
        value = re.sub(r"[^a-z0-9]+", " ", value)
        return re.sub(r"\s+", " ", value).strip()

    @classmethod
    def _find_column(cls, columns, logical_name):
        normalized = {cls.normalize_column(column): column for column in columns}
        aliases = cls.COLUMN_ALIASES[logical_name]
        for alias in aliases:
            alias_norm = cls.normalize_column(alias)
            if alias_norm in normalized:
                return normalized[alias_norm]
        for normalized_column, original in normalized.items():
            if any(cls.normalize_column(alias) in normalized_column for alias in aliases):
                return original
        return None

    @classmethod
    def _decimal(cls, value):
        if value is None or (isinstance(value, float) and pd.isna(value)):
            return None
        text = cls.normalize_text(value)
        if not text or text.lower() in {"-", "--", "n.a.", "na", "n/a"}:
            return None
        text = text.replace(",", "").replace("₹", "").replace("%", "")
        text = re.sub(r"[^0-9.\-()]+", "", text)
        if text.startswith("(") and text.endswith(")"):
            text = "-" + text[1:-1]
        try:
            return Decimal(text)
        except (InvalidOperation, ValueError):
            return None

    @classmethod
    def normalize_security_key(cls, security_name, isin=None):
        isin = cls.normalize_text(isin).upper()
        if isin and isin not in {"-", "NA", "N/A"}:
            return f"ISIN:{isin}"
        name = cls.normalize_text(security_name).upper()
        name = re.sub(r"[^A-Z0-9]+", " ", name)
        # Keep the regex out of the f-string expression. Python 3.11 rejects
        # backslashes in f-string expression parts even when inside a regex.
        normalized_name = re.sub(r"\s+", " ", name).strip()
        return f"NAME:{normalized_name}"

    @classmethod
    def _extract_date(cls, text):
        text = cls.normalize_text(text)
        patterns = [
            r"(?P<d>\d{1,2})[-_/](?P<m>\d{1,2})[-_/](?P<y>20\d{2})",
            r"(?P<d>\d{1,2})[- ](?P<m>[A-Za-z]{3,9})[- ](?P<y>20\d{2})",
            r"(?P<y>20\d{2})[-_/](?P<m>\d{1,2})[-_/](?P<d>\d{1,2})",
        ]
        for pattern in patterns:
            match = re.search(pattern, text)
            if not match:
                continue
            try:
                groups = match.groupdict()
                if groups["m"].isdigit():
                    return date(int(groups["y"]), int(groups["m"]), int(groups["d"]))
                try:
                    return datetime.strptime(
                        f"{groups['d']} {groups['m']} {groups['y']}", "%d %B %Y"
                    ).date()
                except ValueError:
                    return datetime.strptime(
                        f"{groups['d']} {groups['m']} {groups['y']}", "%d %b %Y"
                    ).date()
            except ValueError:
                continue
        return None

    @classmethod
    def _response_content(cls, response):
        response.raise_for_status()
        return response.content

    @classmethod
    def _candidate_urls(cls, scheme):
        """Return official disclosure URLs discovered from the AMFI portal."""
        # The public AMFI portfolio-disclosure page is the authoritative index.
        # AMC-specific discovery is intentionally handled by parsing links from
        # that page rather than hard-coding unstable third-party endpoints.
        return [cls.AMFI_DISCLOSURE_URL]

    @classmethod
    def _get(cls, url):
        return requests.get(
            url,
            headers=cls._headers(),
            timeout=cls.TIMEOUT_SECONDS,
        )

    @classmethod
    def _parse_html_tables(cls, content, fallback_date=None):
        try:
            tables = pd.read_html(io.BytesIO(content))
        except (ValueError, ImportError):
            return []
        records = []
        for dataframe in tables:
            records.extend(cls._parse_dataframe(dataframe, fallback_date=fallback_date))
        return records

    @classmethod
    def parse_document(cls, content, filename, fallback_date=None):
        filename_lower = filename.lower()
        if filename_lower.endswith((".xls", ".xlsx")):
            tables = pd.read_excel(io.BytesIO(content), sheet_name=None)
            records = []
            for dataframe in tables.values():
                records.extend(cls._parse_dataframe(dataframe, fallback_date=fallback_date))
            return records
        if filename_lower.endswith(".csv"):
            return cls._parse_dataframe(
                pd.read_csv(io.BytesIO(content)), fallback_date=fallback_date
            )
        return cls._parse_html_tables(content, fallback_date=fallback_date)

    @classmethod
    def _parse_dataframe(cls, dataframe, fallback_date=None):
        dataframe = dataframe.dropna(how="all").copy()
        if dataframe.empty:
            return []
        dataframe.columns = [cls.normalize_text(column) for column in dataframe.columns]
        name_col = cls._find_column(dataframe.columns, "security_name")
        if not name_col:
            return []
        isin_col = cls._find_column(dataframe.columns, "isin")
        quantity_col = cls._find_column(dataframe.columns, "quantity")
        value_col = cls._find_column(dataframe.columns, "market_value")
        pct_col = cls._find_column(dataframe.columns, "percentage_of_nav")

        rows = []
        for _, row in dataframe.iterrows():
            security_name = cls.normalize_text(row.get(name_col))
            if not security_name:
                continue
            lower = security_name.lower()
            if lower in {"total", "grand total", "total investments"} or lower.startswith("total "):
                continue
            isin = cls.normalize_text(row.get(isin_col)) if isin_col else None
            quantity = cls._decimal(row.get(quantity_col)) if quantity_col else None
            market_value = cls._decimal(row.get(value_col)) if value_col else None
            percentage = cls._decimal(row.get(pct_col)) if pct_col else None
            if percentage is None or percentage < 0 or percentage > 100:
                continue
            rows.append({
                "security_name": security_name,
                "isin": isin or None,
                "security_key": cls.normalize_security_key(security_name, isin),
                "quantity": quantity,
                "market_value": market_value,
                "percentage_of_nav": percentage,
                "portfolio_date": fallback_date,
            })
        return rows

    @classmethod
    @transaction.atomic
    def import_document(cls, scheme, content, filename, source_reference, fallback_date=None):
        records = cls.parse_document(content, filename, fallback_date=fallback_date)
        if not records:
            raise ValueError(f"No valid portfolio rows found in {filename}.")
        portfolio_date = next((row["portfolio_date"] for row in records if row["portfolio_date"]), None)
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
        """Fetch the latest official disclosure for one scheme.

        The actual discovery/parsing implementation is delegated to the
        official-source adapter so the common persistence rules remain stable.
        """
        from .official_underlying import OfficialMutualFundUnderlyingService
        return OfficialMutualFundUnderlyingService.fetch_scheme(scheme)

    @classmethod
    def fetch_all_active(cls, owner_ids=None):
        filters = {"is_active": True}
        if owner_ids is not None:
            filters["owner_id__in"] = owner_ids
        schemes = MutualFundScheme.objects.filter(**filters)
        results = []
        for scheme in schemes.iterator():
            try:
                results.append(cls.fetch_scheme(scheme))
            except Exception as exc:
                logger.exception("MF underlying fetch failed for scheme %s", scheme.pk)
                results.append({"status": "error", "scheme_id": scheme.pk, "error": str(exc)})
        return results
