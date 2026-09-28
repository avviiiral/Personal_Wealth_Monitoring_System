import csv
import io
import os
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import requests
import yfinance as yf
from django.utils import timezone

from mutual_funds.models import AMFIMasterNAV
from mutual_funds.services.amfi import AMFIService
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
        "BSE 500": "BSE500T",
    }

    BSE500_FILE = os.getenv(
        "WATCHLIST_BENCHMARK_BSE500_FILE",
        str(Path(__file__).resolve().parents[1] / "data" / "bse500_tri.csv"),
    )
    # Optional override for deployments that have a licensed BSE500 TRI feed.
    BSE500_URL = os.getenv("WATCHLIST_BENCHMARK_BSE500_URL", "").strip()
    BSE500_API = "https://api.bseindia.com/BseIndiaAPI/api/ProduceCSVForDate/w"
    # Public-market fallback: HDFC's ETF explicitly tracks the BSE 500 TRI.
    # This keeps Watch List benchmark comparison automatic when BSE's public
    # historical endpoint does not expose the TRI series directly.
    BSE_TRI_PROXY_TICKERS = ("HDFCBSE500.NS", "BSE500IETF.NS")
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

        # yfinance's DataFrame/Series typing varies across its releases.
        # Keep the runtime behavior unchanged while making the boundary
        # explicit to Pylance.
        close: Any = data["Adj Close"] if "Adj Close" in data else data["Close"]
        if hasattr(close, "columns"):
            close = close.iloc[:, 0]

        points = []
        for index, value in close.dropna().items():
            try:
                index_date = index.date()
                points.append(
                    {"date": index_date.isoformat(), "value": float(value)}
                )
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
            raise ValueError("BSE 500 TRI source has no CSV header")

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
                    "value",
                )
                if key in normalized_fields
            ),
            None,
        )
        if not date_field or not value_field:
            raise ValueError(
                "BSE 500 TRI source must contain Date and Close/Index Value columns"
            )

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
                raise ValueError(
                    f"BSE 500 TRI row {row_number} has invalid date: {raw_date!r}"
                )

            try:
                numeric_value = float(str(raw_value).replace(",", "").strip())
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"BSE 500 TRI row {row_number} has invalid value: {raw_value!r}"
                ) from exc

            if numeric_value <= 0:
                raise ValueError(
                    f"BSE 500 TRI row {row_number} has non-positive value"
                )
            if point_date in seen:
                raise ValueError(
                    f"BSE 500 TRI contains duplicate date: {point_date.isoformat()}"
                )
            if previous is not None and point_date <= previous:
                raise ValueError("BSE 500 TRI dates must be strictly increasing")

            seen.add(point_date)
            previous = point_date
            points.append(
                {"date": point_date.isoformat(), "value": numeric_value}
            )

        return points

    @classmethod
    def _parse_bse_api_csv(cls, text):
        """Parse BSE's historical CSV and require the BSE500T identity when supplied."""
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
                identity in {"BSE500T", "BSE 500 TRI", "BSE 500 TOTAL RETURN INDEX"}
                or "BSE500T" in identity
                or "BSE 500 TRI" in identity
                for identity in identities
            ):
                raise ValueError(
                    "BSE historical endpoint did not return the requested BSE500T "
                    "Total Return Index series"
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
                for key in (
                    "close",
                    "closevalue",
                    "indexvalue",
                    "indexlevel",
                    "value",
                )
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
                "strIndex": "BSE500T",
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
            raise ValueError("BSE500T historical endpoint returned no observations")
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

        # First try the BSE historical endpoint. If the public endpoint does
        # not provide BSE500T, fall back to exchange-traded products that
        # explicitly track the BSE 500 TRI. This requires no manual CSV.
        try:
            points = cls._fetch_bse_history(start)
            if points:
                return points
        except Exception as bse_error:
            last_error = bse_error
        else:
            last_error = None

        for ticker in cls.BSE_TRI_PROXY_TICKERS:
            try:
                points = cls._series(ticker, start)
                if points:
                    return points
            except Exception as proxy_error:
                last_error = proxy_error

        if last_error:
            raise ValueError(
                f"BSE 500 TRI data unavailable from BSE and public TRI-tracking "
                f"fallbacks: {last_error}"
            ) from last_error
        raise ValueError("BSE 500 TRI data source returned no observations")

    @classmethod
    def _benchmark_series(cls, benchmark, start):
        if benchmark == "BSE 500":
            return cls._bse_series(start)
        return cls._series(cls._ticker(benchmark), start)

    @staticmethod
    def _period_return_detail(points, days, annualize_long_periods=True):
        if not points:
            return None

        points = sorted(points, key=lambda point: point["date"])
        end = points[-1]
        end_date = date.fromisoformat(end["date"])
        cutoff = end_date - timedelta(days=days)
        eligible = [
            point
            for point in points
            if date.fromisoformat(point["date"]) <= cutoff
        ]
        if not eligible:
            return None

        start = eligible[-1]
        start_date = date.fromisoformat(start["date"])
        start_value = float(start["value"])
        end_value = float(end["value"])
        if start_value <= 0 or end_value <= 0:
            return None

        elapsed_days = max((end_date - start_date).days, 1)
        ratio = end_value / start_value
        if days > 365 and annualize_long_periods:
            return {
                "return": (ratio ** (365.25 / elapsed_days) - 1.0) * 100.0,
                "start_date": start["date"],
                "end_date": end["date"],
                "start_value": start_value,
                "end_value": end_value,
                "elapsed_days": elapsed_days,
                "method": "CAGR",
            }

        return {
            "return": (ratio - 1.0) * 100.0,
            "start_date": start["date"],
            "end_date": end["date"],
            "start_value": start_value,
            "end_value": end_value,
            "elapsed_days": elapsed_days,
            "method": "Cumulative return",
        }

    @classmethod
    def _period_return(cls, points, days):
        detail = cls._period_return_detail(points, days)
        return detail["return"] if detail else None

    @staticmethod
    def _is_idcw_option(option):
        normalized = str(option or "").strip().upper()
        return "IDCW" in normalized

    @classmethod
    def _fund_metrics(cls, product):
        # Mutual-fund returns are rebuilt from AMFI NAV observations for
        # Growth options. IDCW NAV history cannot produce investor total
        # returns because distributions reduce NAV.
        mutual_fund = getattr(product, "mutual_fund", None)
        if (
            getattr(product, "product_type", None) == "MUTUAL_FUND"
            and cls._is_idcw_option(getattr(mutual_fund, "option", None))
        ):
            snapshots = list(
                PerformanceSnapshot.objects.filter(
                    product=product,
                    source="AMFI",
                )
                .exclude(nav_or_value__isnull=True)
                .order_by("date", "id")
            )
            latest = snapshots[-1] if snapshots else None
            return (
                {period: None for period in cls.PERIOD_DAYS},
                {period: None for period in cls.PERIOD_DAYS},
                latest,
            )

        if getattr(product, "product_type", None) == "MUTUAL_FUND":
            snapshots = list(
                PerformanceSnapshot.objects.filter(
                    product=product,
                    source="AMFI",
                )
                .exclude(nav_or_value__isnull=True)
                .order_by("date", "id")
            )
            points = [
                {
                    "date": snapshot.date.isoformat(),
                    "value": float(snapshot.nav_or_value),
                }
                for snapshot in snapshots
                if snapshot.nav_or_value and float(snapshot.nav_or_value) > 0
            ]
            details = {
                period: cls._period_return_detail(points, days)
                for period, days in cls.PERIOD_DAYS.items()
            }
            metrics = {
                period: detail["return"] if detail else None
                for period, detail in details.items()
            }
            latest = snapshots[-1] if snapshots else None
            return metrics, details, latest

        # PMS/provider-reported returns are retained because nav_or_value is
        # not necessarily a portfolio NAV from which a return can be rebuilt.
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
        details = {period: None for period in fields}
        return metrics, details, latest

    @staticmethod
    def _fund_scheme_code(product):
        if getattr(product, "product_type", None) != "MUTUAL_FUND":
            return None
        mutual_fund = getattr(product, "mutual_fund", None)
        return str(
            getattr(mutual_fund, "scheme_code", None)
            or getattr(product, "external_identifier", None)
            or ""
        ).strip() or None

    @classmethod
    def _ensure_master_history(cls, product, start, end):
        """Backfill the shared AMFI master only for the requested scheme/window.

        AMFI's public historical endpoint accepts at most 90 days per request.
        The response contains all schemes, so the importer filters it before
        writing; no per-user AMFI download or family-level NAV duplication is
        performed.
        """
        scheme_code = cls._fund_scheme_code(product)
        if not scheme_code or start > end:
            return

        # Do not make a network call when a window already contains a normal
        # business-day density of observations. The latest NAV import can leave
        # only one observation in the most recent window, so checking only for
        # existence is insufficient.
        window_start = start
        while window_start <= end:
            window_end = min(window_start + timedelta(days=89), end)
            expected_minimum = max(
                5,
                int((window_end - window_start).days * 0.5),
            )
            existing_count = AMFIMasterNAV.objects.filter(
                scheme__scheme_code=scheme_code,
                source="AMFI",
                date__gte=window_start,
                date__lte=window_end,
            ).count()

            if existing_count < expected_minimum:
                AMFIService.import_historical_master_navs(
                    window_start,
                    window_end,
                    scheme_codes={scheme_code},
                )

            window_start = window_end + timedelta(days=1)

    @classmethod
    def _fund_series(cls, product, start, end=None):
        """Return the daily fund series needed by the relative-performance chart.

        Mutual funds use the shared AMFI master as the primary source. If the
        master does not yet contain enough observations for the requested
        window, fall back to the already-imported AMFI PerformanceSnapshot
        history for this product. This is a read-only fallback: it does not
        trigger a per-user AMFI download and keeps the chart usable while the
        shared master is being populated.
        """
        end = end or timezone.now().date()

        if getattr(product, "product_type", None) == "MUTUAL_FUND":
            scheme_code = cls._fund_scheme_code(product)
            if scheme_code:
                # Benchmark-performance is a read-only API and must never
                # block on an external AMFI download. Shared AMFI history is
                # populated by the scheduler/management command; this request
                # only reads what is already available locally.
                master_points = list(
                    AMFIMasterNAV.objects.filter(
                        scheme__scheme_code=scheme_code,
                        source="AMFI",
                        date__gte=start,
                        date__lte=end,
                    )
                    .order_by("date", "id")
                    .values("date", "nav")
                )
                master_series = [
                    {
                        "date": point["date"].isoformat(),
                        "value": float(point["nav"]),
                    }
                    for point in master_points
                    if point["nav"] and float(point["nav"]) > 0
                ]
                if len(master_series) >= 2:
                    return master_series

            # Compatibility fallback for existing AMFI history. The chart
            # should not disappear merely because the shared master has not
            # yet been backfilled for this scheme.
            snapshots = (
                PerformanceSnapshot.objects.filter(
                    product=product,
                    source="AMFI",
                    date__gte=start,
                    date__lte=end,
                )
                .exclude(nav_or_value__isnull=True)
                .order_by("date", "id")
            )
            return [
                {
                    "date": snapshot.date.isoformat(),
                    "value": float(snapshot.nav_or_value),
                }
                for snapshot in snapshots
                if snapshot.nav_or_value and float(snapshot.nav_or_value) > 0
            ]

        snapshots = (
            PerformanceSnapshot.objects.filter(
                product=product,
                date__gte=start,
                date__lte=end,
            )
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
    def calculate_benchmark(cls, benchmark):
        if benchmark not in cls.TICKERS:
            return {
                "benchmark": benchmark,
                "available": False,
                "message": "Unsupported benchmark.",
            }

        max_days = cls.PERIOD_DAYS["5Y"] + 31
        start = timezone.now().date() - timedelta(days=max_days)
        try:
            series = cls._benchmark_series(benchmark, start)
        except Exception:
            series = []

        if not series:
            return {
                "benchmark": benchmark,
                "available": False,
                "benchmark_metrics": {
                    period: None for period in cls.PERIOD_DAYS
                },
                "message": "Benchmark market data is not available from the configured data source.",
            }

        details = {
            period: cls._period_return_detail(series, days)
            for period, days in cls.PERIOD_DAYS.items()
        }
        metrics = {
            period: detail["return"] if detail else None
            for period, detail in details.items()
        }
        return {
            "benchmark": benchmark,
            "available": True,
            "as_of_date": series[-1]["date"],
            "benchmark_metrics": metrics,
            "benchmark_return_details": details,
            "benchmark_cagr_3y": metrics.get("3Y"),
            "benchmark_cagr_5y": metrics.get("5Y"),
        }

    @staticmethod
    def _normalized_chart_series(fund_points, benchmark_points, days):
        """Build a synchronized, independently normalized chart series.

        Mutual-fund NAV dates and exchange-index dates can differ by a
        business day. Do not require exact date equality: for each benchmark
        observation use the latest fund NAV on or before that date, provided
        it is no more than three calendar days old. This avoids dropping an
        otherwise valid daily fund series simply because the two data sources
        publish the observation on different dates.
        """
        fund = sorted(
            (
                point
                for point in fund_points
                if point.get("date") and point.get("value") is not None
            ),
            key=lambda point: point["date"],
        )
        benchmark = sorted(
            (
                point
                for point in benchmark_points
                if point.get("date") and point.get("value") is not None
            ),
            key=lambda point: point["date"],
        )
        if not fund or not benchmark:
            return None

        end_date = min(
            date.fromisoformat(fund[-1]["date"]),
            date.fromisoformat(benchmark[-1]["date"]),
        )
        requested_start = end_date - timedelta(days=days)

        fund_window = [
            point
            for point in fund
            if requested_start - timedelta(days=3)
            <= date.fromisoformat(point["date"]) <= end_date
            and float(point["value"]) > 0
        ]
        benchmark_window = [
            point
            for point in benchmark
            if requested_start <= date.fromisoformat(point["date"]) <= end_date
            and float(point["value"]) > 0
        ]
        if not fund_window or not benchmark_window:
            return None

        paired = []
        fund_index = 0
        latest_fund = None
        for benchmark_point in benchmark_window:
            benchmark_date = date.fromisoformat(benchmark_point["date"])
            while fund_index < len(fund_window):
                candidate = fund_window[fund_index]
                candidate_date = date.fromisoformat(candidate["date"])
                if candidate_date > benchmark_date:
                    break
                latest_fund = candidate
                fund_index += 1

            if latest_fund is None:
                continue

            fund_date = date.fromisoformat(latest_fund["date"])
            if (benchmark_date - fund_date).days > 3:
                continue

            paired.append((benchmark_point, latest_fund))

        if len(paired) < 2:
            return None

        start_date = paired[0][0]["date"]
        effective_end = paired[-1][0]["date"]
        fund_base = float(paired[0][1]["value"])
        benchmark_base = float(paired[0][0]["value"])
        if fund_base <= 0 or benchmark_base <= 0:
            return None

        fund_chart = [
            {
                "date": benchmark_point["date"],
                "value": float(fund_point["value"]) / fund_base * 100.0,
            }
            for benchmark_point, fund_point in paired
        ]
        benchmark_chart = [
            {
                "date": benchmark_point["date"],
                "value": float(benchmark_point["value"]) / benchmark_base * 100.0,
            }
            for benchmark_point, _ in paired
        ]

        return {
            "fund": fund_chart,
            "benchmark": benchmark_chart,
            "start_date": start_date,
            "end_date": effective_end,
        }

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

        fund_metrics, fund_return_details, latest = cls._fund_metrics(product)
        is_idcw = (
            getattr(product, "product_type", None) == "MUTUAL_FUND"
            and cls._is_idcw_option(
                getattr(getattr(product, "mutual_fund", None), "option", None)
            )
        )
        # Benchmark returns are always reported as the actual cumulative
        # change over the selected observation window. Do not annualize the
        # 3Y/5Y benchmark values into CAGR.
        benchmark_return_details = {
            period: cls._period_return_detail(
                benchmark_series,
                days,
                annualize_long_periods=False,
            )
            for period, days in cls.PERIOD_DAYS.items()
        }
        benchmark_metrics = {
            period: detail["return"] if detail else None
            for period, detail in benchmark_return_details.items()
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
        comparison = {}
        for period in cls.PERIOD_DAYS:
            difference = differences.get(period)
            if difference is None:
                comparison[period] = "Unavailable"
            elif difference > 0:
                comparison[period] = "Outperformed"
            elif difference < 0:
                comparison[period] = "Underperformed"
            else:
                comparison[period] = "In line"

        chart_days = cls.PERIOD_DAYS.get(
            chart_period, cls.PERIOD_DAYS["1Y"]
        )

        # Keep chart availability consistent with the selected period's
        # comparison result. For example, if the fund has no stored 5Y
        # return, do not display a shorter history and label it as 5Y.
        if (
            fund_metrics.get(chart_period) is None
            or benchmark_metrics.get(chart_period) is None
        ):
            normalized_chart = None
        else:
            chart_start = timezone.now().date() - timedelta(days=chart_days + 10)
            benchmark_chart = [
                point
                for point in benchmark_series
                if date.fromisoformat(point["date"]) >= chart_start
            ]
            fund_chart = cls._fund_series(
                product,
                chart_start,
                end=timezone.now().date(),
            )
            normalized_chart = cls._normalized_chart_series(
                fund_chart,
                benchmark_chart,
                chart_days,
            )

        return {
            "benchmark": benchmark,
            "available": True,
            "as_of_date": benchmark_series[-1]["date"],
            "fund_metrics": fund_metrics,
            "fund_return_details": fund_return_details,
            "fund_return_basis": (
                "Unavailable: IDCW NAV history does not include distributions; "
                "a distribution-adjusted total-return series is required."
                if is_idcw
                else "AMFI NAV observations"
            ),
            "benchmark_metrics": benchmark_metrics,
            "benchmark_return_details": benchmark_return_details,
            "differences": differences,
            "comparison": comparison,
            "chart_period": chart_period,
            "chart": normalized_chart or {
                "fund": [],
                "benchmark": [],
                "start_date": None,
                "end_date": None,
            },
        }