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
    an idempotent upsert; an older portfolio date is never overwritten.
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
            if any(alias in normalized_column for alias in aliases):
                return original
        return None

    @classmethod
    def _decimal(cls, value):
        if value is None or (isinstance(value, float) and pd.isna(value)):
            return None
        text = cls.normalize_text(value)
        if not text or text in {"-", "--", "n.a.", "na", "n/a"}:
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
        return f"NAME:{re.sub(r'\s+', ' ', name).strip()}"

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
                return datetime.strptime(
                    f"{groups['d']} {groups['m']} {groups['y']}", "%d %B %Y"
                ).date()
            except ValueError:
                try:
                    return datetime.strptime(
                        f"{groups['d']} {groups['m']} {groups['y']}", "%d %b %Y"
                    ).date()
                except ValueError:
                    continue
        return None

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
            if security_name.isdigit():
                continue

            isin = cls.normalize_text(row.get(isin_col)) if isin_col else None
            quantity = cls._decimal(row.get(quantity_col)) if quantity_col else None
            market_value = cls._decimal(row.get(value_col)) if value_col else None
            percentage = cls._decimal(row.get(pct_col)) if pct_col else None
            if percentage is None:
                continue
            if percentage < 0 or percentage > 100:
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
    def parse_document(cls, content, filename, fallback_date=None):
        portfolio_date = cls._extract_date(filename) or fallback_date
        lower_name = filename.lower()
        frames = []

        if lower_name.endswith((".xlsx", ".xls")):
            workbook = pd.ExcelFile(io.BytesIO(content))
            for sheet in workbook.sheet_names:
                try:
                    frames.append(pd.read_excel(workbook, sheet_name=sheet, header=0))
                except Exception:
                    logger.debug("Unable to parse portfolio sheet %s", sheet, exc_info=True)
        elif lower_name.endswith(".csv"):
            frames.append(pd.read_csv(io.BytesIO(content)))
        else:
            try:
                frames.extend(pd.read_html(io.BytesIO(content)))
            except (ValueError, ImportError):
                try:
                    frames.extend(pd.read_html(content.decode("utf-8", errors="ignore")))
                except (ValueError, ImportError):
                    frames = []

        records = []
        for frame in frames:
            records.extend(cls._parse_dataframe(frame, portfolio_date))
        return records

    @classmethod
    def _official_links(cls, html, base_url):
        links = []
        for href in re.findall(r"(?:href|data-href)=[\"']([^\"']+)[\"']", html, re.I):
            absolute = urljoin(base_url, href.strip())
            parsed = urlparse(absolute)
            if parsed.scheme not in {"http", "https"}:
                continue
            links.append(absolute)
        return list(dict.fromkeys(links))

    @classmethod
    def _matches_scheme(cls, url, scheme):
        haystack = cls.normalize_text(url).lower()
        candidates = [scheme.scheme_code, scheme.isin_growth, scheme.isin_dividend, scheme.scheme_name]
        for candidate in candidates:
            candidate = cls.normalize_text(candidate).lower() if candidate else ""
            if candidate and len(candidate) >= 4 and candidate in haystack:
                return True
        tokens = [token for token in re.split(r"[^a-z0-9]+", scheme.scheme_name.lower()) if len(token) >= 5]
        return len(tokens) >= 2 and sum(token in haystack for token in tokens) >= min(3, len(tokens))

    @classmethod
    def _fetch(cls, url):
        response = requests.get(url, headers=cls._headers(), timeout=cls.TIMEOUT_SECONDS)
        response.raise_for_status()
        return response

    @classmethod
    def discover_documents(cls, scheme):
        """
        Discover downloadable portfolio files from the official AMFI
        disclosure index and official AMC pages linked from it.
        No third-party portfolio provider is accepted.
        """
        index_response = cls._fetch(cls.AMFI_DISCLOSURE_URL)
        links = cls._official_links(index_response.text, cls.AMFI_DISCLOSURE_URL)

        candidates = []
        for link in links:
            lower = link.lower()
            if lower.endswith((".xlsx", ".xls", ".csv")):
                if cls._matches_scheme(link, scheme):
                    candidates.append(link)
                continue
            if cls._matches_scheme(link, scheme) or scheme.amc_name and cls.normalize_text(scheme.amc_name).lower() in lower:
                try:
                    page = cls._fetch(link)
                except requests.RequestException:
                    continue
                for child in cls._official_links(page.text, link):
                    child_lower = child.lower()
                    if child_lower.endswith((".xlsx", ".xls", ".csv", ".pdf")) and cls._matches_scheme(child, scheme):
                        candidates.append(child)

        return list(dict.fromkeys(candidates))

    @classmethod
    @transaction.atomic
    def import_document(cls, scheme, content, filename, source_reference, fallback_date=None):
        records = cls.parse_document(content, filename, fallback_date=fallback_date)
        if not records:
            raise ValueError(f"No valid portfolio rows found in {filename}.")

        portfolio_date = next((row["portfolio_date"] for row in records if row["portfolio_date"]), None)
        if portfolio_date is None:
            raise ValueError(f"Could not determine portfolio date for {filename}.")

        # A disclosure is immutable by portfolio date. Re-fetching the same
        # file is allowed, but never deletes or changes another disclosure date.
        existing_count = MutualFundUnderlying.objects.filter(
            scheme=scheme,
            portfolio_date=portfolio_date,
            source=cls.SOURCE,
        ).count()
        if existing_count:
            return {"status": "already_imported", "portfolio_date": portfolio_date, "records": existing_count}

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
        return {"status": "imported", "portfolio_date": portfolio_date, "records": len(objects)}

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
                filename = urlparse(document_url).path.rsplit("/", 1)[-1] or "portfolio.xlsx"
                result = cls.import_document(
                    scheme,
                    response.content,
                    filename,
                    document_url,
                    fallback_date=timezone.localdate(),
                )
                return result
            except Exception as exc:
                last_error = exc
                logger.warning("Portfolio import failed for %s from %s: %s", scheme.scheme_name, document_url, exc)

        raise ValueError(f"All official portfolio disclosures failed for {scheme.scheme_name}: {last_error}")

    @classmethod
    def fetch_all_active(cls, owner_ids=None):
        queryset = MutualFundScheme.objects.filter(is_active=True).order_by("id")
        if owner_ids is not None:
            queryset = queryset.filter(owner_id__in=list(owner_ids))

        results = {"schemes": 0, "imported": 0, "already_imported": 0, "failed": 0, "errors": []}
        for scheme in queryset:
            results["schemes"] += 1
            try:
                result = cls.fetch_scheme(scheme)
                if result["status"] == "imported":
                    results["imported"] += 1
                else:
                    results["already_imported"] += 1
            except Exception as exc:
                results["failed"] += 1
                results["errors"].append({"scheme_id": scheme.id, "scheme_name": scheme.scheme_name, "error": str(exc)})
        return results
