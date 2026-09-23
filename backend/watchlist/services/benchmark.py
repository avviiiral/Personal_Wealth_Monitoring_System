import csv
import io
import os
from datetime import date, datetime, timedelta
from pathlib import Path

import requests
import yfinance as yf
from django.utils import timezone

from watchlist.models import PerformanceSnapshot


class BenchmarkPerformanceService:
    """Calculate Watch List benchmark comparisons from market/index time series."""

    PERIOD_DAYS = {
        "1M": 31,
        "3M": 92,
        "6M": 184,
        "1Y": 365,
        "3Y": 365 * 3,
        "5Y": 365 * 5,
    }

    TICKERS = {
        "Nifty 50": "^NSEI",
        "BSE 500": "BSE500",
    }

    BSE500_FILE = os.getenv(
        "WATCHLIST_BENCHMARK_BSE500_FILE",
        str(Path(__file__).resolve().parents[1] / "data" / "bse500.csv"),
    )
    # Optional override for deployments that have a licensed BSE500 feed.
    BSE500_URL = os.getenv("WATCHLIST_BENCHMARK_BSE500_URL", "").strip()
    BSE500_API = "https://api.bseindia.com/BseIndiaAPI/api/ProduceCSVForDate/w"
    BSE_HEADERS = {
        "Accept": "text/csv,application/json,text/plain,*/*",
        "Referer": "https://www.bseindia.com/",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/140.0 Safari/537.36"
        ),
    }

    @classmethod
    def _ticker(cls, benchmark):
        return cls.TICKERS[benchmark]

    @staticmethod
    def _series(ticker, start):
        data = yf.download(
            ticker,
            start=start.isoformat(),
            end=(timezone.now().date() + timedelta(days=1)).isoformat(),
            auto_adjust=False,
            progress=False,
            threads=False,
        )
        if data is None or data.empty:
            return []

        close = data["Adj Close"] if "Adj Close" in data else data["Close"]
        if hasattr(close, "columns"):
            close = close.iloc[:, 0]
        points = []
        for index, value in close.dropna().items():
            try:
                points.append({"date": index.date().isoformat(), "value": float(value)})
            except (AttributeError, TypeError, ValueError):
                continue
        return points

    @staticmethod
    def _parse_date(value):
        if value is None:
            return None
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value

        text = str(value).strip()
        for fmt in (
            "%Y-%m-%d",
            "%d/%m/%Y",
            "%d-%m-%Y",
            "%d-%b-%Y",
            "%d %b %Y",
            "%d-%B-%Y",
            "%d %B %Y",
            "%Y/%m/%d",
        ):
            try:
                return datetime.strptime(text, fmt).date()
            except ValueError:
                continue
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
        except ValueError:
            return None

    @classmethod
    def _load_bse_csv(cls, text):
        reader = csv.DictReader(io.StringIO(text))
        if not reader.fieldnames:
            raise ValueError("BSE 500 source has no CSV header")

        normalized_fields = {
            str(field).strip().lower().replace(" ", "").replace("_", ""): field
            for field in reader.fieldnames
            if field
        }
        date_field = next(
            (
                normalized_fields[key]
                for key in ("date", "indexdate", "tradedate", "tradingdate")
                if key in normalized_fields
            ),
            None,
        )
        value_field = next(
            (
                normalized_fields[key]
                for key in (
                    "close",
                    "closevalue",
                    "indexvalue",
                    "indexlevel",
                    "indexvalue",
                    "value",
                )
                if key in normalized_fields
            ),
            None,
        )
        if not date_field or not value_field:
            raise ValueError(
                "BSE 500 source must contain Date and Close/Index Value columns"
            )

        points = []
        seen = set()
        previous = None
        for row_number, row in enumerate(reader, start=2):
            raw_date = row.get(date_field)
            raw_value = row.get(value_field)
            if raw_date in (None, "") or raw_value in (None, ""):
                raise ValueError(f"BSE 500 row {row_number} has missing date/value")

            point_date = cls._parse_date(raw_date)
            if point_date is None:
                raise ValueError(
                    f"BSE 500 row {row_number} has invalid date: {raw_date!r}"
                )

            try:
                numeric_value = float(str(raw_value).replace(",", "").strip())
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"BSE 500 row {row_number} has invalid value: {raw_value!r}"
                ) from exc

            if numeric_value <= 0:
                raise ValueError(
                    f"BSE 500 row {row_number} has non-positive value"
                )
            if point_date in seen:
                raise ValueError(
                    f"BSE 500 contains duplicate date: {point_date.isoformat()}"
                )
            if previous is not None and point_date <= previous:
                raise ValueError("BSE 500 dates must be strictly increasing")

            seen.add(point_date)
            previous = point_date
            points.append(
                {"date": point_date.isoformat(), "value": numeric_value}
            )

        return points

    @classmethod
    def _parse_bse_api_csv(cls, text):
        """Parse BSE's historical CSV while requiring the requested BSE500 identity when supplied."""
        reader = csv.DictReader(io.StringIO(text))
        if not reader.fieldnames:
            return []

        fields = {
            str(field).strip().lower().replace(" ", "").replace("_", ""): field
            for field in reader.fieldnames
            if field
        }
        index_field = next(
            (
                fields[key]
                for key in ("indexname", "index", "indexcode", "symbol")
                if key in fields
            ),
            None,
        )
        rows = list(reader)
        if index_field:
            identities = {
                str(row.get(index_field, "")).strip().upper()
                for row in rows
                if row.get(index_field) not in (None, "")
            }
            if identities and not any(
                identity == "BSE500"
                or "BSE 500" in identity
                or "BSE500" in identity
                for identity in identities
            ):
                raise ValueError(
                    "BSE historical endpoint did not return the requested BSE500 price-return series"
                )

        date_field = next(
            (
                fields[key]
                for key in ("date", "indexdate", "tradedate", "tradingdate")
                if key in fields
            ),
            None,
        )
        value_field = next(
            (
                fields[key]
                for key in ("close", "closevalue", "indexvalue", "indexlevel", "value")
                if key in fields
            ),
            None,
        )
        if not date_field or not value_field:
            return []

        points = []
        for row in rows:
            point_date = cls._parse_date(row.get(date_field))
            raw_value = row.get(value_field)
            if point_date is None or raw_value in (None, ""):
                continue
            try:
                value = float(str(raw_value).replace(",", "").strip())
            except (TypeError, ValueError):
                continue
            if value > 0:
                points.append({"date": point_date.isoformat(), "value": value})

        points.sort(key=lambda point: point["date"])
        deduped = {}
        for point in points:
            deduped[point["date"]] = point
        return list(deduped.values())

    @classmethod
    def _fetch_bse_points(cls, start, end):
        if cls.BSE500_URL:
            response = requests.get(
                cls.BSE500_URL,
                headers=cls.BSE_HEADERS,
                timeout=45,
            )
            response.raise_for_status()
            return cls._load_bse_csv(response.text)

        response = requests.get(
            cls.BSE500_API,
            params={
                "strIndex": "BSE500",
                "dtFromDate": start.strftime("%d/%m/%Y"),
                "dtToDate": end.strftime("%d/%m/%Y"),
                "period": "D",
            },
            headers=cls.BSE_HEADERS,
            timeout=45,
        )
        response.raise_for_status()
        points = cls._parse_bse_api_csv(response.text)
        if not points:
            raise ValueError("BSE500 historical endpoint returned no observations")
        return points

    @classmethod
    def _fetch_bse_history(cls, start):
        end = timezone.now().date()
        all_points = {}

        # BSE's historical endpoint is queried in bounded annual windows.
        cursor = start
        while cursor <= end:
            window_end = min(cursor + timedelta(days=365), end)
            for point in cls._fetch_bse_points(cursor, window_end):
                point_date = date.fromisoformat(point["date"])
                if start <= point_date <= end:
                    all_points[point["date"]] = point
            cursor = window_end + timedelta(days=1)

        return [all_points[key] for key in sorted(all_points)]

    @classmethod
    def _bse_series(cls, start):
        """Load only BSE500 price-return data; never substitute BSE500 price return."""
        source_file = Path(cls.BSE500_FILE)
        if source_file.exists():
            points = cls._load_bse_csv(
                source_file.read_text(encoding="utf-8-sig")
            )
            return [
                point
                for point in points
                if date.fromisoformat(point["date"]) >= start
            ]

        return cls._fetch_bse_history(start)

    @classmethod
    def _benchmark_series(cls, benchmark, start):
        if benchmark == "BSE 500":
            return cls._bse_series(start)
        return cls._series(cls._ticker(benchmark), start)

    @staticmethod
    def _period_return(points, days):
        if not points:
            return None
        cutoff = date.fromisoformat(points[-1]["date"]) - timedelta(days=days)
        eligible = [
            point
            for point in points
            if date.fromisoformat(point["date"]) <= cutoff
        ]
        if not eligible:
            return None
        start = eligible[-1]
        end = points[-1]
        if not start["value"] or not end["value"]:
            return None
        ratio = end["value"] / start["value"]
        if days > 365:
            elapsed = max(
                (
                    date.fromisoformat(end["date"])
                    - date.fromisoformat(start["date"])
                ).days,
                1,
            )
            return (ratio ** (365.25 / elapsed) - 1.0) * 100.0
        return (ratio - 1.0) * 100.0

    @staticmethod
    def _fund_metrics(product):
        snapshots = list(
            PerformanceSnapshot.objects.filter(product=product)
            .order_by("-date", "-id")
        )
        fields = {
            "1M": "return_1m",
            "3M": "return_3m",
            "6M": "return_6m",
            "1Y": "return_1y",
            "3Y": "return_3y",
            "5Y": "return_5y",
        }
        metrics = {}
        for period, field in fields.items():
            value = next(
                (
                    getattr(snapshot, field)
                    for snapshot in snapshots
                    if getattr(snapshot, field) is not None
                ),
                None,
            )
            metrics[period] = float(value) if value is not None else None
        latest = snapshots[0] if snapshots else None
        return metrics, latest

    @staticmethod
    def _fund_series(product, start):
        snapshots = (
            PerformanceSnapshot.objects.filter(product=product, date__gte=start)
            .exclude(nav_or_value__isnull=True)
            .order_by("date", "id")
        )
        return [
            {"date": snapshot.date.isoformat(), "value": float(snapshot.nav_or_value)}
            for snapshot in snapshots
            if snapshot.nav_or_value and float(snapshot.nav_or_value) > 0
        ]

    @staticmethod
    def _product_benchmark(product):
        if not product:
            return None
        if getattr(product, "product_type", None) == "MUTUAL_FUND":
            mutual_fund = getattr(product, "mutual_fund", None)
            benchmark = getattr(mutual_fund, "benchmark", None) if mutual_fund else None
            return "BSE 500" if benchmark == "BSE 500 TRI" else benchmark
        if getattr(product, "product_type", None) == "PMS":
            pms = getattr(product, "pms", None)
            benchmark = getattr(pms, "benchmark", None) if pms else None
            return "BSE 500" if benchmark == "BSE 500 TRI" else benchmark
        return None

    @classmethod
    def calculate(cls, product, chart_period="1Y"):
        benchmark = cls._product_benchmark(product)
        if not benchmark or benchmark not in cls.TICKERS:
            return None

        max_days = cls.PERIOD_DAYS["5Y"] + 31
        start = timezone.now().date() - timedelta(days=max_days)
        try:
            benchmark_series = cls._benchmark_series(benchmark, start)
        except Exception:
            benchmark_series = []

        if not benchmark_series:
            return {
                "benchmark": benchmark,
                "available": False,
                "message": "Benchmark market data is not available from the configured data source.",
            }

        fund_metrics, latest = cls._fund_metrics(product)
        benchmark_metrics = {
            period: cls._period_return(benchmark_series, days)
            for period, days in cls.PERIOD_DAYS.items()
        }
        differences = {
            period: (
                fund_metrics[period] - benchmark_metrics[period]
                if fund_metrics.get(period) is not None
                and benchmark_metrics.get(period) is not None
                else None
            )
            for period in cls.PERIOD_DAYS
        }
        comparison = {
            period: (
                "Outperformed"
                if differences[period] > 0
                else "Underperformed"
                if differences[period] < 0
                else "In line"
            )
            if differences[period] is not None
            else "Unavailable"
            for period in cls.PERIOD_DAYS
        }

        chart_days = cls.PERIOD_DAYS.get(
            chart_period, cls.PERIOD_DAYS["1Y"]
        )
        chart_start = timezone.now().date() - timedelta(days=chart_days + 10)
        benchmark_chart = [
            point
            for point in benchmark_series
            if date.fromisoformat(point["date"]) >= chart_start
        ]
        fund_chart = cls._fund_series(product, chart_start)

        return {
            "benchmark": benchmark,
            "available": True,
            "as_of_date": benchmark_series[-1]["date"],
            "fund_metrics": fund_metrics,
            "benchmark_metrics": benchmark_metrics,
            "differences": differences,
            "comparison": comparison,
            "benchmark_cagr_3y": benchmark_metrics.get("3Y"),
            "benchmark_cagr_5y": benchmark_metrics.get("5Y"),
            "chart_period": chart_period,
            "chart": {
                "fund": fund_chart,
                "benchmark": benchmark_chart,
            },
        }


class BenchmarkDataRefreshService:
    """Refresh BSE500 automatically and persist a validated local cache."""

    @classmethod
    def refresh_bse500(cls, start=None):
        service = BenchmarkPerformanceService
        start = start or (
            timezone.now().date()
            - timedelta(days=service.PERIOD_DAYS["5Y"] + 31)
        )
        source_file = Path(service.BSE500_FILE)

        # Reuse the existing cache and fetch only the missing tail on normal runs.
        if source_file.exists():
            cached = service._load_bse_csv(
                source_file.read_text(encoding="utf-8-sig")
            )
            if cached:
                latest = date.fromisoformat(cached[-1]["date"])
                fetch_start = max(start, latest - timedelta(days=7))
            else:
                cached = []
                fetch_start = start
        else:
            cached = []
            fetch_start = start

        points = service._fetch_bse_history(fetch_start)
        merged = {point["date"]: point for point in cached if date.fromisoformat(point["date"]) >= start}
        merged.update(point for point in points if date.fromisoformat(point["date"]) >= start)
        ordered = [merged[key] for key in sorted(merged)]

        if not ordered:
            return {
                "available": False,
                "updated": 0,
                "reason": "BSE500 historical endpoint returned no observations.",
            }

        source_file.parent.mkdir(parents=True, exist_ok=True)
        source_file.write_text(
            "Date,Close\n"
            + "\n".join(
                f"{point['date']},{point['value']}" for point in ordered
            )
            + "\n",
            encoding="utf-8",
        )
        return {
            "available": True,
            "updated": len(ordered),
            "as_of_date": ordered[-1]["date"],
        }
