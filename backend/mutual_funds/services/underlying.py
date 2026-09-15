import io
import logging
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from html import unescape
from urllib.parse import parse_qs, unquote, urljoin, urlparse

import pandas as pd
from curl_cffi import requests as curl_requests
from django.db import transaction

from investments.models import Asset, AssetCategory, Holding
from mutual_funds.models import MutualFundScheme, MutualFundUnderlying

logger = logging.getLogger(__name__)


class MutualFundUnderlyingService:
    """
    Import monthly/fortnightly mutual-fund portfolio disclosures from
    official AMFI/AMC sources.

    Discovery is deliberately data-driven. It uses the AMC name, scheme code,
    ISIN and scheme name already stored in MutualFundScheme and discovers the
    official AMC page at runtime. No individual AMC, scheme, ISIN or AMC URL is
    hard-coded here.
    """

    AMFI_DISCLOSURE_URL = "https://www.amfiindia.com/online-center/portfolio-disclosure"
    SOURCE = "AMFI"
    TIMEOUT_SECONDS = 60
    SEARCH_TIMEOUT_SECONDS = 20
    MAX_SEARCH_RESULTS = 10
    MAX_DETAIL_LINKS = 12

    COLUMN_ALIASES = {
        "security_name": {
            "name", "name of instrument", "name of the instrument", "security",
            "security name", "instrument", "instrument name", "issuer",
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
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://www.google.com/",
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
            html_text = content.decode("utf-8", errors="ignore") if isinstance(content, bytes) else str(content)
            try:
                frames.extend(pd.read_html(io.StringIO(html_text)))
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
            absolute = urljoin(base_url, unescape(href.strip()))
            parsed = urlparse(absolute)
            if parsed.scheme not in {"http", "https"}:
                continue
            links.append(absolute)
        return list(dict.fromkeys(links))

    @classmethod
    def _scheme_identifiers(cls, scheme):
        values = [scheme.scheme_code, scheme.isin_growth, scheme.isin_dividend, scheme.scheme_name]
        return [cls.normalize_text(value).lower() for value in values if cls.normalize_text(value)]

    @classmethod
    def _matches_scheme(cls, text, scheme):
        haystack = cls.normalize_text(text).lower()
        candidates = cls._scheme_identifiers(scheme)
        for candidate in candidates[:3]:
            if len(candidate) >= 4 and candidate in haystack:
                return True

        tokens = [
            token for token in re.split(r"[^a-z0-9]+", scheme.scheme_name.lower())
            if len(token) >= 5
        ]
        if len(tokens) < 2:
            return False
        return sum(token in haystack for token in tokens) >= min(3, len(tokens))

    @classmethod
    def _amc_tokens(cls, scheme):
        text = cls.normalize_text(getattr(scheme, "amc_name", "")).lower()
        text = re.sub(
            r"\b(asset management company|asset management|mutual fund|mutual funds|amc|limited|ltd|private|pvt)\b",
            " ",
            text,
        )
        return [token for token in re.split(r"[^a-z0-9]+", text) if len(token) >= 4]

    @classmethod
    def _host_looks_like_amc(cls, host, scheme):
        host = host.lower().split(":", 1)[0]
        tokens = cls._amc_tokens(scheme)
        return any(token in host for token in tokens)

    @classmethod
    def _fetch(cls, url):
        response = curl_requests.get(
            url,
            headers=cls._headers(),
            timeout=cls.TIMEOUT_SECONDS,
            impersonate="chrome",
            allow_redirects=True,
        )
        response.raise_for_status()
        return response

    @classmethod
    def _extract_search_result_urls(cls, html):
        urls = []
        for raw_href in re.findall(r"(?:href|data-href)=[\"']([^\"']+)[\"']", html, re.I):
            href = unescape(raw_href)
            parsed = urlparse(href)
            if parsed.path in {"/url", "/l/", "/link"}:
                target = (
                    parse_qs(parsed.query).get("q", [None])[0]
                    or parse_qs(parsed.query).get("uddg", [None])[0]
                    or parse_qs(parsed.query).get("url", [None])[0]
                )
                if target:
                    href = unquote(target)
            parsed = urlparse(href)
            if parsed.scheme not in {"http", "https"}:
                continue
            host = parsed.netloc.lower()
            if any(search_host in host for search_host in ("google.", "duckduckgo.com", "bing.com")):
                continue
            urls.append(href)
        return list(dict.fromkeys(urls))

    @classmethod
    def _search_urls(cls, query):
        """Discover URLs through public search engines using browser-like HTTP."""
        urls = []
        endpoints = (
            "https://www.google.com/search",
            "https://www.bing.com/search",
            "https://html.duckduckgo.com/html/",
        )

        for endpoint in endpoints:
            try:
                response = curl_requests.get(
                    endpoint,
                    params={"q": query, "num": cls.MAX_SEARCH_RESULTS, "count": cls.MAX_SEARCH_RESULTS, "hl": "en"},
                    headers=cls._headers(),
                    timeout=cls.SEARCH_TIMEOUT_SECONDS,
                    impersonate="chrome",
                    allow_redirects=True,
                )
                response.raise_for_status()
            except Exception as exc:
                logger.debug("Search endpoint failed: %s", endpoint, exc_info=exc)
                continue

            urls.extend(cls._extract_search_result_urls(response.text))
            urls = list(dict.fromkeys(urls))
            if urls:
                break

        return urls[: cls.MAX_SEARCH_RESULTS]

    @classmethod
    def _search_official_pages(cls, scheme):
        identifiers = cls._scheme_identifiers(scheme)
        scheme_name = cls.normalize_text(scheme.scheme_name)
        amc_name = cls.normalize_text(getattr(scheme, "amc_name", ""))
        isin = scheme.isin_growth or scheme.isin_dividend or ""
        scheme_code = scheme.scheme_code or ""

        queries = [
            f'"{scheme_name}" "{isin}" portfolio',
            f'"{scheme_name}" "{scheme_code}" portfolio',
            f'"{scheme_name}" "portfolio disclosure"',
            f'"{amc_name}" "{scheme_name}" holdings',
        ]
        if isin:
            queries.append(f'"{isin}" "portfolio" mutual fund')
        if scheme_code:
            queries.append(f'"{scheme_code}" "portfolio" mutual fund')
        queries.append(f'site:amfiindia.com "{scheme_name}" "{isin}"')

        pages = []
        seen_urls = set()
        seen_hosts = set()

        for query in queries:
            for url in cls._search_urls(query):
                if url in seen_urls:
                    continue
                seen_urls.add(url)

                parsed = urlparse(url)
                host = parsed.netloc.lower()
                if not cls._host_looks_like_amc(host, scheme) and "amfiindia.com" not in host:
                    continue

                lower_url = url.lower()
                is_document = lower_url.endswith((".xlsx", ".xls", ".csv", ".pdf"))
                if is_document and cls._matches_scheme(url, scheme):
                    pages.append((url, ""))
                    continue

                if host in seen_hosts:
                    continue
                try:
                    response = cls._fetch(url)
                except Exception:
                    continue

                body = response.text
                if not cls._matches_scheme(body, scheme):
                    continue
                if not re.search(r"portfolio|holding|disclosure|investment", body, re.I):
                    continue

                pages.append((url, body))
                seen_hosts.add(host)

        return pages

    @classmethod
    def _find_download_links(cls, page_url, html, scheme):
        candidates = []
        for link in cls._official_links(html, page_url):
            lower = link.lower()
            if not lower.endswith((".xlsx", ".xls", ".csv", ".pdf")):
                continue
            if cls._matches_scheme(link, scheme) or cls._matches_scheme(html, scheme):
                candidates.append(link)
        return candidates

    @classmethod
    def discover_documents(cls, scheme):
        """Discover the latest official AMFI/AMC portfolio page or file."""
        candidates = []

        # First inspect the AMFI disclosure index. This remains the preferred
        # official source and does not assume any AMC URL structure.
        try:
            amfi_response = cls._fetch(cls.AMFI_DISCLOSURE_URL)
            amfi_links = cls._official_links(amfi_response.text, cls.AMFI_DISCLOSURE_URL)
            for link in amfi_links:
                if cls._matches_scheme(link, scheme):
                    candidates.append(link)
        except Exception:
            logger.warning("Unable to access AMFI portfolio disclosure page", exc_info=True)

        # Runtime search discovers the official AMC host from the stored AMC
        # name. No individual AMC domain or fund URL is encoded in the code.
        for page_url, html in cls._search_official_pages(scheme):
            if page_url.lower().endswith((".xlsx", ".xls", ".csv", ".pdf")):
                candidates.append(page_url)
                continue

            candidates.extend(cls._find_download_links(page_url, html, scheme))
            candidates.append(page_url)

            detail_links = []
            for child in cls._official_links(html, page_url):
                parsed = urlparse(child)
                if parsed.netloc.lower() != urlparse(page_url).netloc.lower():
                    continue
                if child.lower().endswith((".xlsx", ".xls", ".csv", ".pdf")):
                    candidates.append(child)
                    continue
                if cls._matches_scheme(child, scheme):
                    detail_links.append(child)

            for child in detail_links[: cls.MAX_DETAIL_LINKS]:
                try:
                    detail = cls._fetch(child)
                except Exception:
                    continue
                if not cls._matches_scheme(detail.text, scheme):
                    continue
                candidates.extend(cls._find_download_links(child, detail.text, scheme))
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
        MutualFundUnderlying.objects.bulk_create(objects, ignore_conflicts=True, batch_size=500)
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
    def fetch_scheme(cls, scheme):
        """Discover and import the latest official disclosure for one scheme."""
        raise NotImplementedError(
            "fetch_scheme must be implemented by a MutualFundUnderlyingService subclass."
        )

    @classmethod
    def fetch_all_active(cls, owner_ids=None):
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
                results["errors"].append({
                    "owner_id": owner_id,
                    "scheme_id": scheme.id,
                    "scheme_name": scheme.scheme_name,
                    "error": str(exc),
                })
        return results
