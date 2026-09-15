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

from investments.models import Asset, AssetCategory, Holding
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

    # Official AMC disclosure landing pages used as a fallback when AMFI's
    # portfolio page is rendered dynamically and therefore exposes no file
    # links to a normal HTTP client. Only official AMC domains are allowed.
    OFFICIAL_AMC_DISCLOSURE_URLS = {
        "bandhan": "https://bandhanmutual.com/statutory-disclosures/scheme-portfolios/fortnightly",
        "hdfc": "https://www.hdfcfund.com/statutory-disclosure/portfolio",
        "icici": "https://www.icicipruamc.com/news-and-media/downloads?currentTabFilter=Other+SchemeDisclosures&subCatTabFilter=Monthly%20Portfolio%20Disclosures",
        "kotak": "https://www.kotakmf.com/Information/Statutory-Disclosures/Portfolio-Disclosures",
    }

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
    def _amc_key(cls, scheme):
        text = cls.normalize_text(getattr(scheme, "amc_name", "")).lower()
        if not text:
            return None
        for key in cls.OFFICIAL_AMC_DISCLOSURE_URLS:
            if key in text:
                return key
        return None

    @classmethod
    def _candidate_pages(cls, scheme):
        urls = []
        amc_key = cls._amc_key(scheme)
        if amc_key:
            urls.append(cls.OFFICIAL_AMC_DISCLOSURE_URLS[amc_key])
        return urls

    @classmethod
    def _find_download_links(cls, page_url, html, scheme):
        candidates = []
        for link in cls._official_links(html, page_url):
            lower = link.lower()
            if not lower.endswith((".xlsx", ".xls", ".csv", ".xlsb", ".zip", ".pdf")):
                continue
            if cls._matches_scheme(link, scheme):
                candidates.append(link)
        return candidates

    @classmethod
    def discover_documents(cls, scheme):
        """Discover portfolio files from official AMFI/AMC pages only."""
        candidates = []

        # AMFI's current portfolio page is client-rendered, so a requests
        # GET can return only the selector shell. Keep AMFI as the primary
        # source, but use the official AMC disclosure page when no links are
        # present in the static HTML.
        try:
            index_response = cls._fetch(cls.AMFI_DISCLOSURE_URL)
            links = cls._official_links(index_response.text, cls.AMFI_DISCLOSURE_URL)
            for link in links:
                lower = link.lower()
                if lower.endswith((".xlsx", ".xls", ".csv")) and cls._matches_scheme(link, scheme):
                    candidates.append(link)
                elif cls._matches_scheme(link, scheme):
                    try:
                        page = cls._fetch(link)
                        candidates.extend(cls._find_download_links(link, page.text, scheme))
                    except requests.RequestException:
                        continue
        except requests.RequestException:
            logger.warning("Unable to access AMFI portfolio disclosure page", exc_info=True)

        # Official AMC fallback. This is important for current holdings because
        # AMFI's page is dynamic and may not expose its download URLs to HTTP.
        for page_url in cls._candidate_pages(scheme):
            try:
                page = cls._fetch(page_url)
            except requests.RequestException:
                continue
            candidates.extend(cls._find_download_links(page_url, page.text, scheme))

            # Some AMC pages use a detail/article link before the actual file.
            for child in cls._official_links(page.text, page_url):
                if child.lower().endswith((".xlsx", ".xls", ".csv", ".pdf")):
                    continue
                if not cls._matches_scheme(child, scheme):
                    continue
                try:
                    detail = cls._fetch(child)
                except requests.RequestException:
                    continue
                candidates.extend(cls._find_download_links(child, detail.text, scheme))

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
    def _portfolio_scheme_pairs(cls, owner_ids=None):
        """Return only MF schemes represented by the user's live portfolio."""
        holdings = Holding.objects.select_related("asset").filter(
            asset__category=AssetCategory.MUTUAL_FUND,
            quantity__gt=0,
        )
        if owner_ids is not None:
            holdings = holdings.filter(owner_id__in=list(owner_ids))

        pairs = []
        seen = set()
        for holding in holdings:
            asset = holding.asset
            schemes = MutualFundScheme.objects.filter(is_active=True)
            if holding.owner_id is not None:
                owner_scoped = schemes.filter(owner_id=holding.owner_id)
                if owner_scoped.exists():
                    schemes = owner_scoped

            scheme = None
            if asset.isin:
                scheme = schemes.filter(isin_growth__iexact=asset.isin).first()
                if scheme is None:
                    scheme = schemes.filter(isin_dividend__iexact=asset.isin).first()
            if scheme is None:
                normalized_asset = cls.normalize_text(asset.name).lower()
                for candidate in schemes.order_by("id"):
                    candidate_name = cls.normalize_text(candidate.scheme_name).lower()
                    if normalized_asset == candidate_name or normalized_asset in candidate_name or candidate_name in normalized_asset:
                        scheme = candidate
                        break
            if scheme is None:
                logger.warning("No MutualFundScheme matched live portfolio asset %s (%s)", asset.id, asset.name)
                continue

            key = (holding.owner_id, scheme.id)
            if key not in seen:
                seen.add(key)
                pairs.append((holding.owner_id, scheme))
        return pairs

    @classmethod
    def fetch_all_active(cls, owner_ids=None):
        # Do not iterate over the entire MutualFundScheme master list. The
        # master contains many schemes that the user does not own, while the
        # actual portfolio is represented by investments.Holding.
        pairs = cls._portfolio_scheme_pairs(owner_ids=owner_ids)
        results = {"schemes": 0, "imported": 0, "already_imported": 0, "failed": 0, "errors": []}
        for owner_id, scheme in pairs:
            results["schemes"] += 1
            try:
                result = cls.fetch_scheme(scheme)
                if result["status"] == "imported":
                    results["imported"] += 1
                else:
                    results["already_imported"] += 1
            except Exception as exc:
                results["failed"] += 1
                results["errors"].append({"owner_id": owner_id, "scheme_id": scheme.id, "scheme_name": scheme.scheme_name, "error": str(exc)})
        return results
