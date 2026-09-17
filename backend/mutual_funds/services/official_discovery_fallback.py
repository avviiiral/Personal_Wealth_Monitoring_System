import re
from urllib.parse import urljoin, urlparse

from .official_underlying import OfficialMutualFundUnderlyingService


class ProductionMutualFundUnderlyingService(OfficialMutualFundUnderlyingService):
    """Official-disclosure service with deterministic AMC discovery fallbacks."""

    AMC_PORTFOLIO_PAGES = {
        "hdfc": (
            "https://www.hdfcfund.com/statutory-disclosure/portfolio/monthly-portfolio",
        ),
        "bandhan": (
            "https://cmsnew.bandhanmutual.com/category/scheme-portfolios/",
            "https://cmsnew.bandhanmutual.com/category/scheme-portfolios/monthly-and-half-yearly/",
            "https://cmsnew.bandhanmutual.com/category/scheme-portfolios/monthly-and-half-yearly/page/2/",
            "https://cmsnew.bandhanmutual.com/category/scheme-portfolios/monthly-and-half-yearly/page/3/",
        ),
        "icici": (
            "https://www.icicipruamc.com/news-and-media/downloads?currentTabFilter=OtherSchemeDisclosures&subCatTabFilter=Monthly%20Portfolio%20Disclosures",
        ),
    }

    SCHEME_PORTFOLIO_PAGES = {
        "kotak liquid fund": (
            "https://www.kotakmf.com/mutual-funds/debt-funds/kotak-liquid-fund/dir-g",
        ),
    }

    @classmethod
    def _fallback_page_urls(cls, scheme):
        name = cls.normalize_text(scheme.scheme_name).lower()
        urls = list(cls.SCHEME_PORTFOLIO_PAGES.get(name, ()))

        amc_name = cls.normalize_text(getattr(scheme, "amc_name", "")).lower()
        for token, page_urls in cls.AMC_PORTFOLIO_PAGES.items():
            if token in amc_name or token in name:
                urls.extend(page_urls)
        return list(dict.fromkeys(urls))

    @staticmethod
    def _valid_http_url(url):
        try:
            parsed = urlparse(url)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                return False
            # Accessing hostname validates bracketed IPv6 syntax as well.
            return bool(parsed.hostname)
        except ValueError:
            return False

    @classmethod
    def _embedded_download_links(cls, html, base_url, scheme):
        if not html or not cls._valid_http_url(base_url):
            return []

        raw_urls = re.findall(
            r'(?:https?:)?//[^\"\'<>\s]+|(?:/|\.\.?/)[^\"\'<>\s]+',
            html,
            re.I,
        )
        links = []
        for raw in raw_urls:
            link = urljoin(base_url, raw.replace("\\/", "/").rstrip("\\"))
            if not cls._valid_http_url(link):
                continue
            lower = link.lower()
            if not lower.endswith((".xlsx", ".xls", ".csv")):
                continue
            if not cls._matches_scheme(link, scheme) and not cls._matches_scheme(html, scheme):
                continue
            links.append(link)
        return list(dict.fromkeys(links))

    @classmethod
    def _collect_fallback_page(cls, page_url, scheme):
        if not cls._valid_http_url(page_url):
            return []
        try:
            response = cls._fetch(page_url)
        except Exception:
            return []

        html = response.text
        candidates = []
        if cls._matches_scheme(html, scheme) and re.search(
            r"portfolio|holding|disclosure|scheme",
            html,
            re.I,
        ):
            candidates.append(page_url)

        candidates.extend(cls._embedded_download_links(html, page_url, scheme))
        links = cls._official_links(html, page_url)
        for link in links:
            if not cls._valid_http_url(link):
                continue
            lower = link.lower()
            if lower.endswith((".xlsx", ".xls", ".csv")):
                if cls._matches_scheme(link, scheme) or cls._matches_scheme(html, scheme):
                    candidates.append(link)
                continue

            link_text = f"{link}"
            if not (
                cls._matches_scheme(link, scheme)
                or re.search(
                    r"portfolio|holding|disclosure|download|monthly|half.?yearly|scheme",
                    link_text,
                    re.I,
                )
            ):
                continue

            try:
                child_response = cls._fetch(link, referer=page_url)
            except Exception:
                continue
            child_html = child_response.text
            candidates.extend(cls._embedded_download_links(child_html, link, scheme))
            for child_link in cls._official_links(child_html, link):
                if not cls._valid_http_url(child_link):
                    continue
                if child_link.lower().endswith((".xlsx", ".xls", ".csv")) and (
                    cls._matches_scheme(child_link, scheme)
                    or cls._matches_scheme(child_html, scheme)
                ):
                    candidates.append(child_link)

        return candidates

    @classmethod
    def discover_documents(cls, scheme):
        # The AMC-specific pages are the authoritative fallback and should be
        # tried before generic search results, which can return stale or
        # unrelated workbook URLs.
        fallback_candidates = []
        for page_url in cls._fallback_page_urls(scheme):
            fallback_candidates.extend(cls._collect_fallback_page(page_url, scheme))

        generic_candidates = list(super().discover_documents(scheme))
        return list(dict.fromkeys(fallback_candidates + generic_candidates))