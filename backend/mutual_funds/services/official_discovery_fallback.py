import re
from html import unescape
from urllib.parse import urljoin, urlparse

from .official_underlying import OfficialMutualFundUnderlyingService


class ProductionMutualFundUnderlyingService(OfficialMutualFundUnderlyingService):
    """Official-disclosure service with deterministic AMC discovery fallbacks."""

    AMC_PORTFOLIO_PAGES = {
        "hdfc": (
            "https://www.hdfcfund.com/statutory-disclosure/portfolio/monthly-portfolio",
        ),
        "bandhan": (
            "https://cmsnew.bandhanmutual.com/category/scheme-portfolios/monthly-and-half-yearly/page/2/",
            "https://cmsnew.bandhanmutual.com/category/scheme-portfolios/monthly-and-half-yearly/page/3/",
            "https://cmsnew.bandhanmutual.com/category/scheme-portfolios/monthly-and-half-yearly/",
        ),
        "icici": (
            "https://www.icicipruamc.com/news-and-media/downloads?currentTabFilter=OtherSchemeDisclosures&subCatTabFilter=Monthly%20Portfolio%20Disclosures",
        ),
    }

    SCHEME_PORTFOLIO_PAGES = {
        "kotak liquid fund": (
            "https://www.kotakmf.com/mutual-funds/debt-funds/kotak-liquid-fund/dir-g",
            "https://www.kotakmf.com/mutual-funds/debt-funds/kotak-liquid-fund/reg-g",
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
            return bool(parsed.hostname)
        except (TypeError, ValueError):
            return False

    @classmethod
    def _clean_link(cls, href, base_url):
        if not href:
            return None
        href = unescape(str(href).strip()).replace("\\/", "/")
        href = href.strip(' \\"\'<>;,')
        if href.startswith(("javascript:", "mailto:", "#")):
            return None
        link = urljoin(base_url, href)
        return link if cls._valid_http_url(link) else None

    @classmethod
    def _scheme_link_match(cls, text, scheme):
        if not text:
            return False
        return cls._matches_scheme(unescape(text), scheme)

    @classmethod
    def _official_links(cls, html, base_url):
        links = []
        if not cls._valid_http_url(base_url):
            return links
        pattern = (
            r"<(?:a|area|button)[^>]*?(?:href|data-href|data-url|data-download|data-file|ng-href)"
            r'''\s*=\s*["']([^"']+)["'][^>]*>.*?</(?:a|area|button)>'''
        )
        for match in re.finditer(pattern, html or "", re.I | re.S):
            link = cls._clean_link(match.group(1), base_url)
            if link:
                links.append(link)
        for href in re.findall(
            r'''(?:href|data-href|data-url|data-download|data-file|ng-href)\s*=\s*["']([^"']+)["']''',
            html or "",
            re.I,
        ):
            link = cls._clean_link(href, base_url)
            if link:
                links.append(link)
        return list(dict.fromkeys(links))

    @classmethod
    def _anchor_download_links(cls, html, base_url, scheme):
        links = []
        pattern = (
            r"<(?:a|area|button)[^>]*?(?:href|data-href|data-url|data-download|data-file|ng-href)"
            r'''\s*=\s*["']([^"']+)["'][^>]*>(.*?)</(?:a|area|button)>'''
        )
        for match in re.finditer(pattern, html or "", re.I | re.S):
            href, inner = match.groups()
            link = cls._clean_link(href, base_url)
            if not link:
                continue
            inner_text = re.sub(r"<[^>]+>", " ", unescape(inner))
            context = f"{inner_text} {link}"
            parsed = urlparse(link)
            if not re.search(r"\.(?:xlsx?|csv|pdf)(?:$|\?)", parsed.path, re.I):
                continue
            if cls._scheme_link_match(context, scheme):
                links.append(link)
        return list(dict.fromkeys(links))

    @classmethod
    def _embedded_download_links(cls, html, base_url, scheme):
        if not html or not cls._valid_http_url(base_url):
            return []

        candidates = []
        patterns = (
            r"""(?:https?:)?//[^"'<>\s]+\.(?:xlsx?|csv|pdf)(?:\?[^"'<>\s]*)?""",
            r"""(?:/|\.\.?/)[^"'<>\s]+\.(?:xlsx?|csv|pdf)(?:\?[^"'<>\s]*)?""",
        )
        for pattern in patterns:
            for raw in re.findall(pattern, html, re.I):
                link = cls._clean_link(raw, base_url)
                if not link or not cls._scheme_link_match(unescape(link), scheme):
                    continue
                candidates.append(link)
        candidates.extend(cls._anchor_download_links(html, base_url, scheme))
        return list(dict.fromkeys(candidates))

    @classmethod
    def _download_candidates_from_page(cls, page_url, html, scheme, depth=0, visited=None):
        if depth > 1:
            return []
        visited = set() if visited is None else visited
        if page_url in visited:
            return []
        visited.add(page_url)

        candidates = cls._embedded_download_links(html, page_url, scheme)
        for link in cls._official_links(html, page_url):
            parsed = urlparse(link)
            lower = link.lower()
            if re.search(r"\.(?:xlsx?|csv|pdf)(?:$|\?)", parsed.path, re.I):
                continue
            if not (
                cls._scheme_link_match(link, scheme)
                or re.search(
                    r"portfolio|holding|disclosure|download|monthly|half.?yearly|scheme",
                    lower,
                    re.I,
                )
            ):
                continue
            try:
                child_response = cls._fetch(link, referer=page_url)
            except Exception:
                continue
            candidates.extend(
                cls._download_candidates_from_page(
                    link,
                    child_response.text,
                    scheme,
                    depth=depth + 1,
                    visited=visited,
                )
            )
        return list(dict.fromkeys(candidates))

    @classmethod
    def _collect_fallback_page(cls, page_url, scheme):
        if not cls._valid_http_url(page_url):
            return []
        try:
            response = cls._fetch(page_url)
        except Exception:
            return []

        html = response.text or ""
        candidates = []
        if cls._scheme_link_match(html, scheme):
            candidates.append(page_url)
        candidates.extend(cls._anchor_download_links(html, page_url, scheme))
        candidates.extend(cls._download_candidates_from_page(page_url, html, scheme))
        return list(dict.fromkeys(candidates))

    @classmethod
    def _search_official_pages(cls, scheme):
        """Reuse generic search while filtering malformed URLs before fetch."""
        return super()._search_official_pages(scheme)

    @classmethod
    def discover_documents(cls, scheme):
        fallback_candidates = []
        for page_url in cls._fallback_page_urls(scheme):
            fallback_candidates.extend(cls._collect_fallback_page(page_url, scheme))

        generic_candidates = []
        # The inherited search code can produce malformed bracketed IPv6 URLs.
        # Run it defensively so one bad result cannot abort the scheme fetch.
        try:
            generic_candidates = [
                url for url in super().discover_documents(scheme)
                if cls._valid_http_url(url)
            ]
        except ValueError:
            generic_candidates = []
        return list(dict.fromkeys(fallback_candidates + generic_candidates))
