from datetime import date, timedelta

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

    # Yahoo Finance exposes Nifty 50 as ^NSEI. BSE's published market-data
    # ticker for the total-return series is BSE500T; deployments may override
    # the Yahoo-compatible ticker if their market-data provider exposes it.
    TICKERS = {
        "Nifty 50": "^NSEI",
        "BSE 500 TRI": "^BSE500T",
    }

    @classmethod
    def _ticker(cls, benchmark):
        if benchmark == "BSE 500 TRI":
            import os
            return os.getenv("WATCHLIST_BENCHMARK_BSE500_TRI_TICKER", cls.TICKERS[benchmark])
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

    @classmethod
    def calculate(cls, product, chart_period="1Y"):
        if not product or not getattr(product, "benchmark", None):
            return None

        benchmark = product.benchmark
        if benchmark not in cls.TICKERS:
            return None

        max_days = cls.PERIOD_DAYS["5Y"] + 31
        start = timezone.now().date() - timedelta(days=max_days)
        try:
            benchmark_series = cls._series(cls._ticker(benchmark), start)
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
