import re
from datetime import datetime
from decimal import Decimal, InvalidOperation

import pandas as pd
import requests
from bs4 import BeautifulSoup
from django.utils import timezone

from watchlist.models import DiscoveryRun, InvestmentProduct, PerformanceSnapshot, PMSProduct, ProductType


class APMIPMSDiscoveryService:
    """Refresh the public PMS investment-approach universe from APMI.

    APMI publishes the investment-approach level PMS data submitted by
    portfolio managers, including provider, approach name, AUM and periodic
    performance. This keeps PMS discovery on the same automatic-universe
    pattern as the existing AMFI mutual-fund refresh without inventing data.
    """

    SOURCE = "APMI"
    REPORT_URL = "https://www.apmiindia.org/apmi/welcomeiaperformance.htm?action=PMSmenu"

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
        match = re.search(r"As on(?: Month-Year)?[^0-9]{0,80}(\d{2}/\d{2}/\d{4})", visible_text, re.IGNORECASE)
        if match:
            try:
                return datetime.strptime(match.group(1), "%d/%m/%Y").date()
            except ValueError:
                pass
        return timezone.now().date()

    @classmethod
    def _tables(cls, html):
        try:
            return pd.read_html(html)
        except (ValueError, ImportError):
            return []

    @classmethod
    def _records(cls, html):
        records = []
        for table in cls._tables(html):
            columns = [cls._text(c).lower() for c in table.columns]
            joined = " | ".join(columns)
            if "pms provider name" not in joined or "ia name" not in joined:
                continue
            for _, row in table.iterrows():
                values = list(row.tolist())
                if len(values) < 3:
                    continue
                provider = cls._text(values[0])
                ia_name = cls._text(values[1])
                aum = cls._decimal(values[2])
                if not provider or not ia_name or provider.lower() == "nan" or ia_name.lower() == "nan":
                    continue
                performance = {}
                for index, period in enumerate(("1m", "3m", "6m", "1y", "2y", "3y", "4y", "5y", "si"), start=3):
                    if index < len(values):
                        performance[period] = cls._decimal(values[index])
                records.append({
                    "provider": provider,
                    "name": ia_name,
                    "aum": aum,
                    "performance": performance,
                })
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
            seen = set()

            for record in records:
                try:
                    identity = f"PMS:APMI:{record['provider'].upper()}:{record['name'].upper()}"
                    if identity in seen:
                        continue
                    seen.add(identity)

                    product, created = InvestmentProduct.objects.update_or_create(
                        identity_key=identity,
                        defaults={
                            "product_type": ProductType.PMS,
                            "name": record["name"],
                            "provider": record["provider"],
                            "country": "India",
                            "category": "PMS",
                            "sub_category": "Investment Approach",
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
