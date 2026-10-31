import logging
import re
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation

import requests
from bs4 import BeautifulSoup
from django.utils import timezone

from config.pwms_config import get as get_pwms_config

from watchlist.models import DiscoveryRun, InvestmentProduct, PerformanceSnapshot, PMSProduct, ProductType

logger = logging.getLogger(__name__)


class APMIPMSDiscoveryService:
    """Refresh the public PMS investment-approach universe from APMI.

    APMI publishes investment-approach level PMS data, including provider,
    approach name, AUM and periodic performance. The report is HTML and its
    table headers can change shape when parsed by pandas, so rows are parsed
    directly from the APMI table markup instead.
    """

    APMI_CONFIG = get_pwms_config("providers", "apmi", {})
    SOURCE = "APMI"
    REPORT_URL = APMI_CONFIG.get("report_url", "")
    PERFORMANCE_PERIODS = tuple(APMI_CONFIG.get("performance_periods", []))
    INSIGHT_URL = "https://www.apmiindia.org/apmi/IaInsight.htm"
    MANAGER_RETRY_AFTER = timedelta(days=7)

    @classmethod
    def manager_details_url(cls, iaid):
        return f"{cls.INSIGHT_URL}?IAID={iaid}"

    @classmethod
    def _extract_fund_manager_names(cls, html):
        """Extract individual names from APMI's Fund Manager Details section."""
        soup = BeautifulSoup(html, "html.parser")
        candidate_tags = soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6", "strong", "b"])
        section_index = next(
            (
                index for index, element in enumerate(candidate_tags)
                if cls._text(element.get_text(" ", strip=True)).casefold() == "fund manager details"
            ),
            None,
        )
        if section_index is None:
            return []

        names = []
        stop_sections = {
            "period-wise performance",
            "period wise performance",
            "ia vs. benchmark performance",
            "turnover details",
        }
        for element in candidate_tags[section_index + 1:]:
            name = cls._text(element.get_text(" ", strip=True))
            normalized = name.casefold()
            if normalized in stop_sections:
                break
            if not name or normalized == "fund manager details":
                continue
            if len(name) > 120 or re.search(r"\b(?:aum|benchmark|strategy name|pms provider|work exp|email id|mobile no)\b", normalized):
                continue
            if name not in names:
                names.append(name)

        # APMI has also rendered the manager cards with bold labels rather
        # than heading tags. Read standalone lines inside the same section as
        # a fallback, while excluding the manager's contact/experience fields.
        if not names:
            visible_lines = [
                cls._text(line)
                for line in soup.get_text("\n", strip=True).splitlines()
            ]
            try:
                start = next(
                    index for index, line in enumerate(visible_lines)
                    if line.casefold() == "fund manager details"
                )
            except StopIteration:
                return []
            for line in visible_lines[start + 1:]:
                normalized = line.casefold()
                if normalized in stop_sections:
                    break
                if (
                    not line
                    or len(line) > 100
                    or re.search(r"\b(?:work exp|email id|mobile no|details|image|aum|benchmark|provider)\b", normalized)
                ):
                    continue
                if re.fullmatch(r"[A-Za-z][A-Za-z.'-]*(?:\s+[A-Za-z][A-Za-z.'-]*){1,5}", line):
                    if line not in names:
                        names.append(line)
        return names

    @classmethod
    def _fetch_fund_manager_details(cls, iaid):
        """Fetch APMI's individual IA detail page and return named managers."""
        url = cls.manager_details_url(iaid)
        try:
            response = requests.get(
                url,
                headers=cls._headers(),
                timeout=cls.APMI_CONFIG.get("manager_timeout_seconds", 20),
            )
            response.raise_for_status()
            names = cls._extract_fund_manager_names(response.text)
            if not names:
                logger.info("APMI IA detail page returned no named managers (IAID=%s, status=%s)", iaid, response.status_code)
            # A successful detail-page response is cached even when APMI has
            # no manager section, preventing the same empty page being fetched
            # on every manual universe refresh.
            return {
                "fund_manager": "; ".join(names) if names else None,
                "website": url,
            }
        except requests.RequestException as exc:
            logger.warning("Unable to retrieve APMI manager details (IAID=%s): %s", iaid, exc)
            return None

    @classmethod
    def _headers(cls):
        configured_agent = str(get_pwms_config("providers", "apmi", {}).get("user_agent", "")).strip()
        browser_agent = (
            configured_agent
            if configured_agent.lower().startswith("mozilla/")
            else "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                 "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        )
        return {
            "User-Agent": browser_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-IN,en;q=0.9",
            "Referer": cls.REPORT_URL,
        }

    @staticmethod
    def _decimal(value):
        if value in (None, "", "-", "NA", "N/A"):
            return None
        try:
            cleaned = str(value).replace("₹", "").replace(",", "").strip()
            return Decimal(cleaned)
        except (InvalidOperation, ValueError, TypeError):
            return None

    @staticmethod
    def _text(value):
        return re.sub(r"\s+", " ", str(value or "").strip())

    @classmethod
    def _report_date(cls, html):
        visible_text = BeautifulSoup(html, "html.parser").get_text(" ", strip=True)
        patterns = (
            r"Investment Approach Wise Performance As on\s+(\d{2}/\d{2}/\d{4})",
            r"As on(?: Month-Year)?[^0-9]{0,80}(\d{2}/\d{2}/\d{4})",
            r"Investment Approach Wise Performance As on\s+(\d{2}/\d{2}/\d{4})",
        )
        for pattern in patterns:
            match = re.search(pattern, visible_text, re.IGNORECASE)
            if match:
                try:
                    return datetime.strptime(match.group(1), "%d/%m/%Y").date()
                except ValueError:
                    continue
        return timezone.now().date()

    @classmethod
    def _records(cls, html):
        """Parse APMI PMS rows without depending on pandas header inference."""
        soup = BeautifulSoup(html, "html.parser")
        records = []
        seen = set()

        for row in soup.find_all("tr"):
            cells = row.find_all("td")
            if len(cells) < 3:
                continue

            ia_link = row.find("a", href=re.compile(r"IaInsight\.htm\?IAID=", re.IGNORECASE))
            if ia_link is None:
                continue

            values = [cls._text(cell.get_text(" ", strip=True)) for cell in cells]
            provider = values[0]
            ia_name = cls._text(ia_link.get_text(" ", strip=True)) or values[1]
            aum = cls._decimal(values[2])

            if not provider or not ia_name or provider.lower() == "nan" or ia_name.lower() == "nan":
                continue

            iaid_match = re.search(r"IAID=([^&#\"']+)", str(ia_link.get("href") or ""), re.IGNORECASE)
            iaid = iaid_match.group(1) if iaid_match else None
            identity = (provider.upper(), iaid or ia_name.upper())
            if identity in seen:
                continue
            seen.add(identity)

            performance = {}
            for index, period in enumerate(cls.PERFORMANCE_PERIODS, start=3):
                if index < len(values):
                    performance[period] = cls._decimal(values[index])

            records.append(
                {
                    "provider": provider,
                    "name": ia_name,
                    "iaid": iaid,
                    "aum": aum,
                    "performance": performance,
                }
            )
        return records

    @classmethod
    def refresh(cls):
        """Refresh APMI PMS data with bulk database upserts."""
        run = DiscoveryRun.objects.create(source=cls.SOURCE)
        discovered = updated = failed = 0
        try:
            response = requests.get(cls.REPORT_URL, headers=cls._headers(), timeout=cls.APMI_CONFIG.get("timeout_seconds", 60))
            response.raise_for_status()
            report_date = cls._report_date(response.text)
            records = cls._records(response.text)

            identities = [
                f"PMS:APMI:{record['provider'].upper()}:{record['iaid'] or record['name'].upper()}"
                for record in records
            ]
            existing_keys = set(
                InvestmentProduct.objects.filter(identity_key__in=identities)
                .values_list("identity_key", flat=True)
            )

            products = []
            for record in records:
                identity_suffix = record["iaid"] or record["name"].upper()
                identity = f"PMS:APMI:{record['provider'].upper()}:{identity_suffix}"
                products.append(
                    InvestmentProduct(
                        identity_key=identity,
                        product_type=ProductType.PMS,
                        name=record["name"],
                        provider=record["provider"],
                        country=cls.APMI_CONFIG.get("country", "India"),
                        category=cls.APMI_CONFIG.get("category", "PMS"),
                        sub_category=cls.APMI_CONFIG.get("sub_category", "Investment Approach"),
                        external_identifier=record["iaid"],
                        currency=cls.APMI_CONFIG.get("currency", "INR"),
                        source=cls.SOURCE,
                        source_reference=cls.REPORT_URL,
                        source_date=report_date,
                        is_active=True,
                    )
                )

            InvestmentProduct.objects.bulk_create(
                products,
                batch_size=1000,
                update_conflicts=True,
                update_fields=[
                    "product_type", "name", "provider", "country", "category",
                    "sub_category", "external_identifier", "currency", "source",
                    "source_reference", "source_date", "is_active", "updated_at",
                ],
                unique_fields=["identity_key"],
            )

            product_by_identity = {
                product.identity_key: product
                for product in InvestmentProduct.objects.filter(
                    identity_key__in=identities
                )
            }

            product_ids = [product.id for product in product_by_identity.values()]
            manager_status_by_id = {
                item.product_id: item
                for item in PMSProduct.objects.filter(product_id__in=product_ids)
            }
            manager_retry_before = timezone.now() - cls.MANAGER_RETRY_AFTER
            manager_records = []
            for record in records:
                if not record["iaid"]:
                    continue
                identity = f"PMS:APMI:{record['provider'].upper()}:{record['iaid']}"
                product = product_by_identity.get(identity)
                pms_product = manager_status_by_id.get(product.id) if product else None
                manager_missing = (
                    pms_product is None
                    or not (pms_product.fund_manager or "").strip()
                )
                retry_due = (
                    pms_product is None
                    or pms_product.fund_manager_checked_at is None
                    or pms_product.fund_manager_checked_at <= manager_retry_before
                )
                if product and manager_missing and retry_due:
                    manager_records.append(record)

            manager_details_by_iaid = {}
            manager_iaids = list(dict.fromkeys(record["iaid"] for record in manager_records))
            if manager_iaids:
                from concurrent.futures import ThreadPoolExecutor

                with ThreadPoolExecutor(max_workers=4) as executor:
                    futures = {
                        executor.submit(cls._fetch_fund_manager_details, iaid): iaid
                        for iaid in manager_iaids
                    }
                    for future, iaid in futures.items():
                        details = future.result()
                        if details:
                            manager_details_by_iaid[iaid] = details

            pms_products = []
            snapshots = []
            for record in records:
                identity_suffix = record["iaid"] or record["name"].upper()
                identity = f"PMS:APMI:{record['provider'].upper()}:{identity_suffix}"
                product = product_by_identity.get(identity)
                if product is None:
                    failed += 1
                    continue

                pms_products.append(
                    PMSProduct(
                        product_id=product.id,
                        strategy_name=record["name"],
                        strategy_type=cls.APMI_CONFIG.get("strategy_type", "Investment Approach"),
                        asset_class=cls.APMI_CONFIG.get("asset_class", "PMS"),
                        aum=record["aum"],
                        latest_value=record["aum"],
                    )
                )
                performance = record["performance"]
                snapshots.append(
                    PerformanceSnapshot(
                        product_id=product.id,
                        date=report_date,
                        source=cls.SOURCE,
                        nav_or_value=record["aum"],
                        aum=record["aum"],
                        return_1m=performance.get("1m"),
                        return_3m=performance.get("3m"),
                        return_6m=performance.get("6m"),
                        return_1y=performance.get("1y"),
                        return_3y=performance.get("3y"),
                        return_5y=performance.get("5y"),
                        return_since_inception=performance.get("si"),
                        source_reference=cls.REPORT_URL,
                    )
                )

            PMSProduct.objects.bulk_create(
                pms_products,
                batch_size=1000,
                update_conflicts=True,
                update_fields=[
                    "strategy_name", "strategy_type", "asset_class",
                    "aum", "latest_value",
                ],
                unique_fields=["product"],
            )

            # Preserve existing person attribution; populate only missing names.
            pms_by_product_id = {
                item.product_id: item
                for item in PMSProduct.objects.filter(product_id__in=product_ids)
            }
            manager_updates = []
            for record in manager_records:
                details = manager_details_by_iaid.get(record["iaid"])
                if not details:
                    # Failed HTTP requests are not cached and can be retried.
                    continue
                identity = f"PMS:APMI:{record['provider'].upper()}:{record['iaid']}"
                product = product_by_identity.get(identity)
                pms_product = pms_by_product_id.get(product.id) if product else None
                if pms_product:
                    if details.get("fund_manager"):
                        pms_product.fund_manager = details["fund_manager"]
                        pms_product.fund_manager_website = details["website"]
                    elif not pms_product.fund_manager_website:
                        pms_product.fund_manager_website = details["website"]
                    pms_product.fund_manager_checked_at = timezone.now()
                    manager_updates.append(pms_product)
            if manager_updates:
                PMSProduct.objects.bulk_update(
                    manager_updates,
                    [
                        "fund_manager",
                        "fund_manager_website",
                        "fund_manager_checked_at",
                    ],
                    batch_size=500,
                )

            PerformanceSnapshot.objects.bulk_create(
                snapshots,
                batch_size=1000,
                update_conflicts=True,
                update_fields=[
                    "nav_or_value", "aum", "return_1m", "return_3m",
                    "return_6m", "return_1y", "return_3y", "return_5y",
                    "return_since_inception", "source_reference", "fetched_at",
                ],
                unique_fields=["product", "date", "source"],
            )

            discovered = len(records)
            updated = len(existing_keys)
            run.discovered = discovered
            run.updated = updated
            run.failed = failed
            run.details = {
                "source_reference": cls.REPORT_URL,
                "report_date": report_date.isoformat(),
                "records_seen": len(records),
                "fund_managers_enriched": sum(
                    bool(item.fund_manager) for item in manager_updates
                ),
                "fund_manager_checks": len(manager_updates),
                "mode": "bulk_upsert",
            }
            run.finished_at = timezone.now()
            run.save(update_fields=["discovered", "updated", "failed", "details", "finished_at"])
            return {
                "discovered": discovered,
                "updated": updated,
                "failed": failed,
                "report_date": report_date.isoformat(),
                "fund_managers_enriched": sum(
                    bool(item.fund_manager) for item in manager_updates
                ),
                "fund_manager_checks": len(manager_updates),
                "source": "APMI",
            }
        except Exception as exc:
            run.failed = max(1, failed)
            run.details = {"source_reference": cls.REPORT_URL, "error": str(exc)}
            run.finished_at = timezone.now()
            run.save(update_fields=["failed", "details", "finished_at"])
            raise
