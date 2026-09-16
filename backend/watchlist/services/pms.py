import re
from datetime import datetime
from decimal import Decimal, InvalidOperation

import requests
from bs4 import BeautifulSoup
from django.utils import timezone

from watchlist.models import DiscoveryRun, InvestmentProduct, PerformanceSnapshot, PMSProduct, ProductType


class APMIPMSDiscoveryService:
    """Refresh the public PMS investment-approach universe from APMI.

    APMI publishes investment-approach level PMS data, including provider,
    approach name, AUM and periodic performance. The report is HTML and its
    table headers can change shape when parsed by pandas, so rows are parsed
    directly from the APMI table markup instead.
    """

    SOURCE = "APMI"
    REPORT_URL = "https://www.apmiindia.org/apmi/welcomeiaperformance.htm?action=PMSmenu"
    PERFORMANCE_PERIODS = ("1m", "3m", "6m", "1y", "2y", "3y", "4y", "5y", "si")

    @staticmethod
    def _headers():
        return {"User-Agent": "PWMS-WatchList/1.0"}

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

            iaid_match = re.search(r"IAID=([^&#\"']+)", ia_link.get("href", ""), re.IGNORECASE)
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
        run = DiscoveryRun.objects.create(source=cls.SOURCE)
        discovered = updated = failed = 0
        try:
            response = requests.get(cls.REPORT_URL, headers=cls._headers(), timeout=60)
            response.raise_for_status()
            report_date = cls._report_date(response.text)
            records = cls._records(response.text)

            for record in records:
                try:
                    identity_suffix = record["iaid"] or record["name"].upper()
                    identity = f"PMS:APMI:{record['provider'].upper()}:{identity_suffix}"

                    product, created = InvestmentProduct.objects.update_or_create(
                        identity_key=identity,
                        defaults={
                            "product_type": ProductType.PMS,
                            "name": record["name"],
                            "provider": record["provider"],
                            "country": "India",
                            "category": "PMS",
                            "sub_category": "Investment Approach",
                            "external_identifier": record["iaid"],
                            "currency": "INR",
                            "source": cls.SOURCE,
                            "source_reference": cls.REPORT_URL,
                            "source_date": report_date,
                            "is_active": True,
                        },
                    )

                    PMSProduct.objects.update_or_create(
                        product=product,
                        defaults={
                            "strategy_name": record["name"],
                            "strategy_type": "Investment Approach",
                            "asset_class": "PMS",
                            "aum": record["aum"],
                            "latest_value": record["aum"],
                        },
                    )

                    PerformanceSnapshot.objects.update_or_create(
                        product=product,
                        date=report_date,
                        source=cls.SOURCE,
                        defaults={
                            "nav_or_value": record["aum"],
                            "aum": record["aum"],
                            "return_1m": record["performance"].get("1m"),
                            "return_3m": record["performance"].get("3m"),
                            "return_6m": record["performance"].get("6m"),
                            "return_1y": record["performance"].get("1y"),
                            "return_3y": record["performance"].get("3y"),
                            "return_5y": record["performance"].get("5y"),
                            "return_since_inception": record["performance"].get("si"),
                            "source_reference": cls.REPORT_URL,
                        },
                    )
                    discovered += 1
                    updated += int(not created)
                except Exception:
                    failed += 1

            run.discovered = discovered
            run.updated = updated
            run.failed = failed
            run.details = {
                "source_reference": cls.REPORT_URL,
                "report_date": report_date.isoformat(),
                "records_seen": len(records),
            }
            run.finished_at = timezone.now()
            run.save(update_fields=["discovered", "updated", "failed", "details", "finished_at"])
            return {
                "discovered": discovered,
                "updated": updated,
                "failed": failed,
                "report_date": report_date.isoformat(),
                "source": cls.SOURCE,
            }
        except Exception as exc:
            run.failed = 1
            run.details = {"source_reference": cls.REPORT_URL, "error": str(exc)}
            run.finished_at = timezone.now()
            run.save(update_fields=["failed", "details", "finished_at"])
            raise
