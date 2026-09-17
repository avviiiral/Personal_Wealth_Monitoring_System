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

from investments.models import AssetCategory, Holding
from mutual_funds.models import MutualFundScheme, MutualFundUnderlying

logger = logging.getLogger(__name__)


class MutualFundUnderlyingService:
    """Generic mutual-fund underlying discovery/import service."""

    AMFI_DISCLOSURE_URL = "https://www.amfiindia.com/online-center/portfolio-disclosure"
    AMFI_SCHEME_DETAILS_URL = "https://www.amfiindia.com/otherdata/scheme-details"
    SOURCE = "AMFI"
    TIMEOUT_SECONDS = 60
    SEARCH_TIMEOUT_SECONDS = 20
    MAX_SEARCH_RESULTS = 10
    MAX_DETAIL_LINKS = 12
    MAX_CRAWL_PAGES = 20

    COLUMN_ALIASES = {
        "security_name": {"name", "name of instrument", "name of the instrument", "security", "security name", "instrument", "instrument name", "issuer", "company", "company name", "stock", "scrip name"},
        "isin": {"isin", "isin code", "isin no", "isin number"},
        "quantity": {"quantity", "qty", "units", "no of shares", "number of shares", "shares", "face value units"},
        "market_value": {"market value", "market value (rs.)", "market value (in rs.)", "fair value", "valuation", "value", "market/fair value"},
        "percentage_of_nav": {"% to nav", "% of nav", "percent of nav", "percentage of nav", "% nav", "nav (%)", "weight", "weight (%)", "portfolio (%)", "percentage"},
    }

    @classmethod
    def _headers(cls, referer="https://www.amfiindia.com/"):
        return {"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8", "Accept-Language": "en-US,en;q=0.9", "Referer": referer}

    @staticmethod
    def _valid_http_url(url):
        try:
            parsed = urlparse(url)
            return parsed.scheme in {"http", "https"} and bool(parsed.netloc) and bool(parsed.hostname)
        except (TypeError, ValueError):
            return False

    @staticmethod
    def normalize_text(value):
        value = "" if value is None else str(value)
        return re.sub(r"\s+", " ", value.replace("\u00a0", " ")).strip()

    @classmethod
    def normalize_column(cls, value):
        value = cls.normalize_text(value).lower().replace("%", "percent")
        return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", value)).strip()

    @classmethod
    def _find_column(cls, columns, logical_name):
        normalized = {cls.normalize_column(column): column for column in columns}
        aliases = [cls.normalize_column(alias) for alias in cls.COLUMN_ALIASES[logical_name]]
        for alias in aliases:
            if alias in normalized:
                return normalized[alias]
        for normalized_column, original in normalized.items():
            if any(alias and alias in normalized_column for alias in aliases):
                return original
        return None

    @classmethod
    def _decimal(cls, value):
        if value is None or (isinstance(value, float) and pd.isna(value)):
            return None
        text = cls.normalize_text(value)
        if not text or text.lower() in {"-", "--", "n.a.", "na", "n/a"}:
            return None
        text = re.sub(r"[^0-9.\-()]+", "", text.replace(",", "").replace("₹", "").replace("%", ""))
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
        name = re.sub(r"[^A-Z0-9]+", " ", cls.normalize_text(security_name).upper())
        return f"NAME:{re.sub(r'\s+', ' ', name).strip()}"

    @classmethod
    def _extract_date(cls, text):
        text = cls.normalize_text(text)
        patterns = [r"(?P<d>\d{1,2})[-_/](?P<m>\d{1,2})[-_/](?P<y>20\d{2})", r"(?P<d>\d{1,2})[- ](?P<m>[A-Za-z]{3,9})[- ](?P<y>20\d{2})", r"(?P<y>20\d{2})[-_/](?P<m>\d{1,2})[-_/](?P<d>\d{1,2})"]
        for pattern in patterns:
            match = re.search(pattern, text)
            if not match:
                continue
            groups = match.groupdict()
            try:
                if groups["m"].isdigit():
                    return date(int(groups["y"]), int(groups["m"]), int(groups["d"]))
                for fmt in ("%d %B %Y", "%d %b %Y"):
                    try:
                        return datetime.strptime(f"{groups['d']} {groups['m']} {groups['y']}", fmt).date()
                    except ValueError:
                        pass
            except ValueError:
                pass
        return None

    @classmethod
    def _scheme_identifiers(cls, scheme):
        values = [scheme.scheme_code, scheme.isin_growth, scheme.isin_dividend, scheme.scheme_name]
        return [cls.normalize_text(value).lower() for value in values if cls.normalize_text(value)]

    @classmethod
    def _scheme_name_tokens(cls, scheme):
        ignored = {"direct", "plan", "growth", "option", "fund", "the"}
        return [t for t in re.split(r"[^a-z0-9]+", cls.normalize_text(scheme.scheme_name).lower()) if len(t) >= 4 and t not in ignored]

    @classmethod
    def _matches_scheme(cls, text, scheme):
        haystack = cls.normalize_text(text).lower()
        identifiers = cls._scheme_identifiers(scheme)
        for candidate in identifiers[:3]:
            if len(candidate) >= 4 and candidate in haystack:
                return True
        tokens = cls._scheme_name_tokens(scheme)
        if len(tokens) < 2:
            return False
        return sum(token in haystack for token in tokens) >= min(3, len(tokens))

    @classmethod
    def _amc_tokens(cls, scheme):
        text = cls.normalize_text(getattr(scheme, "amc_name", "")).lower()
        text = re.sub(r"\b(asset management company|asset management|mutual fund|mutual funds|amc|limited|ltd|private|pvt)\b", " ", text)
        return [token for token in re.split(r"[^a-z0-9]+", text) if len(token) >= 4]

    @classmethod
    def _host_looks_like_amc(cls, host, scheme):
        host = host.lower().split(":", 1)[0]
        return any(token in host for token in cls._amc_tokens(scheme))

    @classmethod
    def _fetch(cls, url, referer=None):
        if not cls._valid_http_url(url):
            raise ValueError(f"Invalid HTTP URL: {url}")
        response = curl_requests.get(url, headers=cls._headers(referer or "https://www.amfiindia.com/"), timeout=cls.TIMEOUT_SECONDS, impersonate="chrome", allow_redirects=True)
        response.raise_for_status()
        return response

    @classmethod
    def _official_links(cls, html, base_url):
        if not cls._valid_http_url(base_url):
            return []
        links = []
        for href in re.findall(r"(?:href|data-href|data-url)=[\"']([^\"']+)[\"']", html or "", re.I):
            absolute = urljoin(base_url, unescape(href.strip()))
            if cls._valid_http_url(absolute):
                links.append(absolute)
        return list(dict.fromkeys(links))

    @classmethod
    def _extract_search_result_urls(cls, html):
        urls = []
        for raw_href in re.findall(r"(?:href|data-href)=[\"']([^\"']+)[\"']", html or "", re.I):
            href = unescape(raw_href)
            try:
                parsed = urlparse(href)
            except (TypeError, ValueError):
                continue
            if parsed.path in {"/url", "/l/", "/link"}:
                target = parse_qs(parsed.query).get("q", [None])[0] or parse_qs(parsed.query).get("uddg", [None])[0] or parse_qs(parsed.query).get("url", [None])[0]
                if target:
                    href = unquote(target)
            if not cls._valid_http_url(href):
                continue
            parsed = urlparse(href)
            if any(x in parsed.netloc.lower() for x in ("google.", "bing.com", "duckduckgo.com")):
                continue
            urls.append(href)
        return list(dict.fromkeys(urls))

    @classmethod
    def _search_urls(cls, query):
        urls = []
        for endpoint in ("https://www.google.com/search", "https://www.bing.com/search", "https://html.duckduckgo.com/html/"):
            try:
                response = curl_requests.get(endpoint, params={"q": query, "num": cls.MAX_SEARCH_RESULTS, "count": cls.MAX_SEARCH_RESULTS, "hl": "en"}, headers=cls._headers(endpoint), timeout=cls.SEARCH_TIMEOUT_SECONDS, impersonate="chrome", allow_redirects=True)
                response.raise_for_status()
                found = cls._extract_search_result_urls(response.text)
            except Exception:
                continue
            urls.extend(found)
            urls = list(dict.fromkeys(urls))
            if urls:
                break
        return urls[: cls.MAX_SEARCH_RESULTS]

    @classmethod
    def _search_official_pages(cls, scheme):
        scheme_name = cls.normalize_text(scheme.scheme_name)
        amc_name = cls.normalize_text(getattr(scheme, "amc_name", ""))
        isin = cls.normalize_text(scheme.isin_growth or scheme.isin_dividend or "")
        code = cls.normalize_text(scheme.scheme_code or "")
        queries = [f'"{scheme_name}" "{isin}" portfolio', f'"{scheme_name}" "{code}" portfolio', f'"{scheme_name}" "portfolio disclosure"', f'"{amc_name}" "{scheme_name}" holdings', f'"{isin}" "portfolio" mutual fund' if isin else f'"{scheme_name}" portfolio mutual fund', f'"{code}" "portfolio" mutual fund' if code else f'"{scheme_name}" portfolio mutual fund']
        pages = []
        seen_urls = set()
        for query in queries:
            for url in cls._search_urls(query):
                if url in seen_urls or not cls._valid_http_url(url):
                    continue
                seen_urls.add(url)
                parsed = urlparse(url)
                host = parsed.netloc.lower()
                if "amfiindia.com" not in host and not cls._host_looks_like_amc(host, scheme):
                    continue
                if url.lower().endswith((".xlsx", ".xls", ".csv", ".pdf")):
                    if cls._matches_scheme(url, scheme):
                        pages.append((url, ""))
                    continue
                try:
                    response = cls._fetch(url)
                except Exception:
                    continue
                body = response.text
                if not (cls._matches_scheme(body, scheme) or cls._matches_scheme(url, scheme)):
                    continue
                if not re.search(r"portfolio|holding|disclosure|investment|scheme", body, re.I):
                    continue
                pages.append((url, body))
        return pages

    @classmethod
    def _find_download_links(cls, page_url, html, scheme):
        return [link for link in cls._official_links(html, page_url) if link.lower().endswith((".xlsx", ".xls", ".csv", ".pdf")) and cls._matches_scheme(link, scheme)]

    @classmethod
    def _discover_amc_domains_from_amfi(cls, scheme):
        domains = []
        for url in (cls.AMFI_DISCLOSURE_URL, cls.AMFI_SCHEME_DETAILS_URL):
            try:
                response = cls._fetch(url)
            except Exception:
                logger.debug("Unable to inspect AMFI page: %s", url, exc_info=True)
                continue
            for link in cls._official_links(response.text, url):
                parsed = urlparse(link)
                if parsed.netloc and cls._host_looks_like_amc(parsed.netloc, scheme):
                    domains.append(f"{parsed.scheme}://{parsed.netloc}")
        return list(dict.fromkeys(domains))
