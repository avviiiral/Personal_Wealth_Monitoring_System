import csv
import io
import os
from pathlib import Path
from datetime import date, datetime, timedelta

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

    # Yahoo Finance exposes Nifty 50 as ^NSEI. BSE publishes the BSE 500
    # total-return series as BSE500T, but Yahoo Finance does not reliably
    # expose that series. BSE 500 TRI therefore uses BSE's own historical
    # index API unless a deployment explicitly configures another provider.
    TICKERS = {
        "Nifty 50": "^NSEI",
        "BSE 500 TRI": "BSE500T",
    }

    # BSE 500 TRI is a licensed total-return series. The public BSE IndexArchDailyAll
    # endpoint exposes the price index, not the TRI, so never fall back to it.
    BSE500_TRI_FILE = os.getenv(
        "WATCHLIST_BENCHMARK_BSE500_TRI_FILE",
        str(Path(__file__).resolve().parents[1] / "data" / "bse500_tri.csv"),
    )
    BSE500_TRI_URL = os.getenv("WATCHLIST_BENCHMARK_BSE500_TRI_URL", "").strip()
    BSE_HEADERS = {
        "Accept": "application/json, text/plain, */*",
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
    def _parse_bse_rows(cls, payload):
        date_keys = {
            "date",
            "indexdate",
            "tradedate",
            "tradingdate",
            "dt",
            "dtdate",
        }
        value_keys = {
            "close",
            "closevalue",
            "closing",
            "closingvalue",
            "indexvalue",
            "indexlevel",
            "value",
            "ltp",
            "prevclose",
        }
        rows = []

        def visit(node):
            if isinstance(node, dict):
                normalized = {
                    str(key).strip().lower().replace(" ", "").replace("_", ""): value
                    for key, value in node.items()
                }
                parsed_date = next(
                    (
                        cls._parse_date(value)
                        for key, value in normalized.items()
                        if key in date_keys and cls._parse_date(value) is not None
                    ),
                    None,
                )
                raw_value = next(
                    (
                        value
                        for key, value in normalized.items()
                        if key in value_keys and value not in (None, "")
                    ),
                    None,
                )
                if parsed_date is not None and raw_value is not None:
                    try:
                        numeric_value = float(str(raw_value).replace(",", "").strip())
                        if numeric_value > 0:
                            rows.append((parsed_date, numeric_value))
                    except (TypeError, ValueError):
                        pass

                for value in node.values():
                    visit(value)
            elif isinstance(node, list):
                for item in node:
                    visit(item)

        visit(payload)
        return rows

    @classmethod
    def _parse_bse_response(cls, response):
        content_type = response.headers.get("Content-Type", "").lower()
        try:
            if "json" in content_type:
                return cls._parse_bse_rows(response.json())
        except (ValueError, TypeError):
            pass

        text = response.text.strip()
        if not text:
            return []

        try:
            return cls._parse_bse_rows(response.json())
        except (ValueError, TypeError):
            pass

        rows = []
        try:
            reader = csv.DictReader(io.StringIO(text))
            for row in reader:
                parsed = cls._parse_bse_rows(row)
                rows.extend(parsed)
        except (csv.Error, UnicodeError):
            return []

        return rows

    @classmethod
    def _load_bse_tri_csv(cls, text):
        reader = csv.DictReader(io.StringIO(text))
        if not reader.fieldnames:
            raise ValueError("BSE 500 TRI source has no CSV header")

        normalized_fields = {
            str(field).strip().lower().replace(" ", "").replace("_", ""): field
            for field in reader.fieldnames
            if field
        }
        date_field = next(
            (normalized_fields[key] for key in ("date", "indexdate", "tradedate") if key in normalized_fields),
            None,
        )
        value_field = next(
            (
                normalized_fields[key]
                for key in (
                    "close", "closevalue", "indexvalue", "indexlevel",
                    "totalreturnindex", "tri", "value",
                )
                if key in normalized_fields
            ),
            None,
        )
        if not date_field or not value_field:
            raise ValueError("BSE 500 TRI CSV must contain Date and Close/Index Value columns")

        points = []
        seen = set()
        previous = None
        for row_number, row in enumerate(reader, start=2):
            raw_date = row.get(date_field)
            raw_value = row.get(value_field)
            if raw_date in (None, "") or raw_value in (None, ""):
                raise ValueError(f"BSE 500 TRI row {row_number} has missing date/value")
            point_date = cls._parse_date(raw_date)
            if point_date is None:
                raise ValueError(f"BSE 500 TRI row {row_number} has invalid date: {raw_date!r}")
            try:
                numeric_value = float(str(raw_value).replace(",", "").strip())
            except (TypeError, ValueError) as exc:
                raise ValueError(f"BSE 500 TRI row {row_number} has invalid value: {raw_value!r}") from exc
            if numeric_value <= 0:
                raise ValueError(f"BSE 500 TRI row {row_number} has non-positive value")
            if point_date in seen:
                raise ValueError(f"BSE 500 TRI contains duplicate date: {point_date.isoformat()}")
            if previous is not None and point_date <= previous:
                raise ValueError("BSE 500 TRI dates must be strictly increasing")
            seen.add(point_date)
            previous = point_date
            points.append({"date": point_date.isoformat(), "value": numeric_value})

        return points

    @classmethod
    def _bse_tri_series(cls, start):
        """Load only a verified BSE 500 TRI series; never substitute BSE 500 PRI."""
        source_file = Path(cls.BSE500_TRI_FILE)
        if source_file.exists():
            points = cls._load_bse_tri_csv(source_file.read_text(encoding="utf-8-sig"))
            return [point for point in points if date.fromisoformat(point["date"]) >= start]

        if not cls.BSE500_TRI_URL:
            return []

        response = requests.get(
            cls.BSE500_TRI_URL,
            headers=cls.BSE_HEADERS,
            timeout=45,
        )
        response.raise_for_status()
        points = cls._load_bse_tri_csv(response.text)
        return [point for point in points if date.fromisoformat(point["date"]) >= start]

    @classmethod
    def _benchmark_series(cls, benchmark, start):
        if benchmark == "BSE 500 TRI":
            # Never use a Yahoo ticker for BSE 500 TRI: a public symbol can
            # resolve to the price index and silently change benchmark semantics.
            return cls._bse_tri_series(start)

        return cls._series(cls._ticker(benchmark), start)

    @staticmethod
    def _period_return(points, days):
        if not points:
            return None
        cutoff = date.fromisoformat(points[-1]["date"]) - timedelta(days=days)
        eligible = [point for point in points if date.fromisoformat(point["date"]) <= cutoff]
        if not eligible:
            return None
        start = eligible[-1]
        end = points[-1]
        if not start["value"] or not end["value"]:
            return None
        ratio = end["value"] / start["value"]
        if days > 365:
            elapsed = max((date.fromisoformat(end["date"]) - date.fromisoformat(start["date"])).days, 1)
            return (ratio ** (365.25 / elapsed) - 1.0) * 100.0
        return (ratio - 1.0) * 100.0

    @staticmethod
    def _fund_metrics(product):
        snapshots = list(
            PerformanceSnapshot.objects.filter(product=product)
            .order_by("-date", "-id")
        )
        latest = snapshots[0] if snapshots else None
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
                (getattr(snapshot, field) for snapshot in snapshots if getattr(snapshot, field) is not None),
                None,
            )
            metrics[period] = float(value) if value is not None else None
        return metrics, latest

    @staticmethod
    def _fund_series(product, start):
        snapshots = (
            PerformanceSnapshot.objects
            .filter(product=product, date__gte=start)
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
            return getattr(mutual_fund, "benchmark", None) if mutual_fund else None
        if getattr(product, "product_type", None) == "PMS":
            pms = getattr(product, "pms", None)
            return getattr(pms, "benchmark", None) if pms else None
        return None

    @classmethod
    def calculate(cls, product, chart_period="1Y"):
        benchmark = cls._product_benchmark(product)
        if not benchmark:
            return None
        if benchmark not in cls.TICKERS:
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
                if fund_metrics.get(period) is not None and benchmark_metrics.get(period) is not None
                else None
            )
            for period in cls.PERIOD_DAYS
        }
        comparison = {
            period: (
                "Outperformed" if differences[period] > 0
                else "Underperformed" if differences[period] < 0
                else "In line"
            ) if differences[period] is not None else "Unavailable"
            for period in cls.PERIOD_DAYS
        }

        chart_days = cls.PERIOD_DAYS.get(chart_period, cls.PERIOD_DAYS["1Y"])
        chart_start = timezone.now().date() - timedelta(days=chart_days + 10)
        benchmark_chart = [
            point for point in benchmark_series
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
    """Refresh the verified BSE 500 TRI source and persist the local cache."""

    @classmethod
    def refresh_bse500_tri(cls, start=None):
        service = BenchmarkPerformanceService
        start = start or (timezone.now().date() - timedelta(days=service.PERIOD_DAYS["5Y"] + 31))
        if not service.BSE500_TRI_URL:
            return {
                "available": False,
                "updated": 0,
                "reason": "WATCHLIST_BENCHMARK_BSE500_TRI_URL is not configured.",
            }
        response = requests.get(
            service.BSE500_TRI_URL,
            headers=service.BSE_HEADERS,
            timeout=45,
        )
        response.raise_for_status()
        points = service._load_bse_tri_csv(response.text)
        points = [point for point in points if date.fromisoformat(point["date"]) >= start]
        if not points:
            return {
                "available": False,
                "updated": 0,
                "reason": "BSE 500 TRI source returned no observations.",
            }
        target = Path(service.BSE500_TRI_FILE)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            "Date,Close\n" + "\n".join(f"{p['date']},{p['value']}" for p in points) + "\n",
            encoding="utf-8",
        )
        return {"available": True, "updated": len(points), "as_of_date": points[-1]["date"]}
