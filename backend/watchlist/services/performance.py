from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation

import requests
from django.db import transaction
from django.utils import timezone

from watchlist.models import InvestmentProduct, PerformanceSnapshot, ProductType


class AMFIPerformanceService:
    """Backfill Watch List MF performance from AMFI historical NAV data.

    AMFI's history endpoint supports date ranges of up to 90 days. The service
    therefore requests only small windows around the required return dates,
    stores the actual NAV observations, and calculates metrics from those
    observations. Missing history is left as null rather than estimated.
    """

    HISTORY_URL = "https://portal.amfiindia.com/DownloadNAVHistoryReport_Po.aspx"
    SOURCE = "AMFI"
    WINDOW_DAYS = 15
    PERIODS = (
        ("return_1m", 1),
        ("return_3m", 3),
        ("return_6m", 6),
        ("return_1y", 12),
        ("return_3y", 36),
        ("return_5y", 60),
    )

    @staticmethod
    def _decimal(value):
        if value in (None, "", "-"):
            return None
        try:
            return Decimal(str(value).replace(",", "").strip())
        except (InvalidOperation, ValueError, TypeError):
            return None

    @staticmethod
    def _subtract_months(value, months):
        year = value.year
        month = value.month - months
        while month <= 0:
            year -= 1
            month += 12
        if month in {4, 6, 9, 11}:
            day = min(value.day, 30)
        elif month == 2:
            leap = year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)
            day = min(value.day, 29 if leap else 28)
        else:
            day = min(value.day, 31)
        return date(year, month, day)

    @classmethod
    def parse_history(cls, text):
        """Parse AMFI historical NAV text across supported column layouts.

        AMFI has changed the historical report layout. The current report is:
        Scheme Code;NAV Name;Plan;Option;ISIN Div Payout/ISIN Growth;
        ISIN Div Reinvestment;Net Asset Value;Date

        Older reports used the ISIN fields immediately after the scheme name
        and placed NAV at column 5. Detect the column positions from the
        header so either layout remains parseable.
        """
        records = []
        positions = {
            "name": 1,
            "plan": None,
            "option": None,
            "isin": 2,
            "isin2": 3,
            "nav": 4,
            "date": 7,
        }

        for raw_line in text.splitlines():
            parts = [str(part).strip().replace("\ufeff", "") for part in raw_line.split(";")]
            normalized = [part.lower() for part in parts]

            if normalized and normalized[0] == "scheme code":
                try:
                    positions["name"] = normalized.index("nav name")
                except ValueError:
                    try:
                        positions["name"] = normalized.index("scheme name")
                    except ValueError:
                        pass
                try:
                    positions["plan"] = normalized.index("plan")
                except ValueError:
                    positions["plan"] = None
                try:
                    positions["option"] = normalized.index("option")
                except ValueError:
                    positions["option"] = None
                try:
                    positions["isin"] = normalized.index("isin div payout/isin growth")
                except ValueError:
                    pass
                try:
                    positions["isin2"] = normalized.index("isin div reinvestment")
                except ValueError:
                    pass
                try:
                    positions["nav"] = normalized.index("net asset value")
                except ValueError:
                    pass
                try:
                    positions["date"] = normalized.index("date")
                except ValueError:
                    pass
                continue

            if len(parts) <= positions["date"] or not parts[0].isdigit():
                continue
            nav = cls._decimal(parts[positions["nav"]])
            if nav is None:
                continue
            try:
                nav_date = datetime.strptime(parts[positions["date"]], "%d-%b-%Y").date()
            except (ValueError, TypeError):
                continue
            records.append(
                {
                    "scheme_code": parts[0],
                    "name": parts[positions["name"]],
                    "plan": parts[positions["plan"]] if positions["plan"] is not None else None,
                    "option": parts[positions["option"]] if positions["option"] is not None else None,
                    "isin": parts[positions["isin"]] if parts[positions["isin"]] not in {"", "-"} else None,
                    "isin2": parts[positions["isin2"]] if parts[positions["isin2"]] not in {"", "-"} else None,
                    "nav": nav,
                    "date": nav_date,
                }
            )
        return records

    @classmethod
    def download_history(cls, start_date, end_date):
        if (end_date - start_date).days > 90:
            raise ValueError("AMFI historical NAV requests cannot exceed 90 days")
        response = requests.get(
            cls.HISTORY_URL,
            params={
                # tp=1 selects AMFI's downloadable text report. Without it,
                # the endpoint can return the WebForms page with HTTP 200,
                # which previously looked like a successful request but
                # contained no parseable NAV rows.
                "tp": "1",
                "frmdt": start_date.strftime("%d-%b-%Y"),
                "todt": end_date.strftime("%d-%b-%Y"),
            },
            headers={"User-Agent": "PWMS-WatchList/1.0"},
            timeout=120,
        )
        response.raise_for_status()
        return response.text

    @staticmethod
    def _nearest_on_or_before(records, target_date):
        eligible = [record for record in records if record["date"] <= target_date]
        if eligible:
            return max(eligible, key=lambda record: record["date"])
        after = [record for record in records if record["date"] > target_date]
        return min(after, key=lambda record: record["date"]) if after else None

    @classmethod
    def _load_required_history(cls, products, latest_date):
        codes = {str(product.external_identifier) for product in products if product.external_identifier}
        if not codes:
            return {}, 0

        selected = {code: {} for code in codes}
        failed = 0
        for field, months in cls.PERIODS:
            target = cls._subtract_months(latest_date, months)
            start = target - timedelta(days=cls.WINDOW_DAYS)
            end = target + timedelta(days=cls.WINDOW_DAYS)
            try:
                records = cls.parse_history(cls.download_history(start, end))
            except Exception:
                failed += 1
                continue
            grouped = {}
            for record in records:
                if record["scheme_code"] in codes:
                    grouped.setdefault(record["scheme_code"], []).append(record)
            for code, rows in grouped.items():
                chosen = cls._nearest_on_or_before(rows, target)
                if chosen:
                    selected[code][field] = chosen
        return selected, failed

    @classmethod
    def _return_percent(cls, latest_nav, historical_nav):
        if latest_nav is None or historical_nav in (None, Decimal("0")):
            return None
        return (latest_nav / historical_nav - Decimal("1")) * Decimal("100")

    @classmethod
    @transaction.atomic
    def refresh(cls):
        products = list(
            InvestmentProduct.objects.filter(
                product_type=ProductType.MUTUAL_FUND,
                is_active=True,
            ).select_related("mutual_fund")
        )
        latest_snapshots = {}
        for snapshot in PerformanceSnapshot.objects.filter(
            product_id__in=[product.id for product in products],
            source=cls.SOURCE,
        ).order_by("-date"):
            latest_snapshots.setdefault(snapshot.product_id, snapshot)

        products_needing_history = [
            product
            for product in products
            if latest_snapshots.get(product.id) is None
            or any(
                getattr(latest_snapshots[product.id], field) is None
                for field, _ in cls.PERIODS
            )
        ]
        if not products_needing_history:
            return {"products": len(products), "history_requests": 0, "snapshots": 0, "metrics_updated": 0, "failed": 0}

        latest_date = (
            max(snapshot.date for snapshot in latest_snapshots.values())
            if latest_snapshots
            else timezone.localdate()
        )
        history, failed = cls._load_required_history(products_needing_history, latest_date)
        snapshots_written = 0
        metrics_updated = 0

        for product in products_needing_history:
            latest = latest_snapshots.get(product.id)
            code = str(
                product.external_identifier
                or getattr(getattr(product, "mutual_fund", None), "scheme_code", "")
                or ""
            )
            periods = history.get(code, {})
            if latest is None and periods:
                latest_record = max(periods.values(), key=lambda record: record["date"])
                latest, _ = PerformanceSnapshot.objects.update_or_create(
                    product=product,
                    date=latest_record["date"],
                    source=cls.SOURCE,
                    defaults={
                        "nav_or_value": latest_record["nav"],
                        "source_reference": cls.HISTORY_URL,
                    },
                )
                latest_snapshots[product.id] = latest

            if latest is None:
                continue

            values = {}
            for field, _ in cls.PERIODS:
                record = periods.get(field)
                if record:
                    PerformanceSnapshot.objects.update_or_create(
                        product=product,
                        date=record["date"],
                        source=cls.SOURCE,
                        defaults={"nav_or_value": record["nav"], "source_reference": cls.HISTORY_URL},
                    )
                    snapshots_written += 1
                    values[field] = cls._return_percent(latest.nav_or_value, record["nav"])

            if values:
                update_fields = []
                for field, value in values.items():
                    setattr(latest, field, value)
                    update_fields.append(field)

                # CAGR uses the longest available annual period. Since the
                # underlying NAV dates are actual market dates, elapsed days
                # are used rather than assuming exactly 365 days per year.
                for field, months in reversed(cls.PERIODS):
                    record = periods.get(field)
                    if not record or record["nav"] in (None, Decimal("0")) or latest.nav_or_value in (None, Decimal("0")):
                        continue
                    years = Decimal(str((latest.date - record["date"]).days)) / Decimal("365.2425")
                    if years > 0:
                        latest.cagr = ((latest.nav_or_value / record["nav"]) ** (Decimal("1") / years) - Decimal("1")) * Decimal("100")
                        update_fields.append("cagr")
                        break

                latest.save(update_fields=list(dict.fromkeys(update_fields)))
                metrics_updated += 1

        return {
            "products": len(products),
            "history_requests": len(cls.PERIODS),
            "snapshots": snapshots_written,
            "metrics_updated": metrics_updated,
            "failed": failed,
        }
