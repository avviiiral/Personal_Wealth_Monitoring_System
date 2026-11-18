import csv
import io
import json
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import requests
import yfinance as yf
from curl_cffi import requests as curl_requests
from django.conf import settings
from django.db import models
from django.utils import timezone

from mutual_funds.models import AMFIMasterNAV
from mutual_funds.services.amfi import AMFIService
from watchlist.models import BenchmarkMasterPoint, PerformanceSnapshot
from config.pwms_config import get as get_pwms_config


class BenchmarkPerformanceService:
    """Calculate Watch List benchmark comparisons from market/index time series."""

    PERIOD_DAYS = get_pwms_config("benchmarks", "period_days", {})
    TICKERS = get_pwms_config("benchmarks", "tickers", {})
    BSE500_FILE = settings.WATCHLIST_BENCHMARK_BSE500_FILE
    BSE500_URL = settings.WATCHLIST_BENCHMARK_BSE500_URL
    BSE500_API = get_pwms_config("benchmarks", "bse_api", "")
    NIFTY_TRI_URLS = tuple(get_pwms_config("benchmarks", "nifty_tri_urls", []))
    NIFTY_TRI_INDICES = dict(get_pwms_config("benchmarks", "nifty_tri_indices", {}))
    SUPPORTED_BENCHMARKS = tuple(NIFTY_TRI_INDICES) + ("BSE 500",)
    NIFTY_TRI_HEADERS = dict(get_pwms_config("benchmarks", "nifty_headers", {}))
    NIFTY_TRI_HEADERS["Referer"] = get_pwms_config("benchmarks", "nifty_historical_page", "")
    BSE_TRI_PROXY_TICKERS = tuple(get_pwms_config("benchmarks", "bse_tri_proxy_tickers", []))
    BSE_TRI_AMFI_SCHEME_CODE = str(
        get_pwms_config("benchmarks", "bse_tri_amfi_scheme_code", "143247")
    )
    BSE_HEADERS = dict(get_pwms_config("benchmarks", "bse_headers", {}))
    MINIMUM_HISTORY_ROWS = int(
        get_pwms_config("benchmarks", "minimum_history_rows", 20)
    )
    MINIMUM_DAILY_COVERAGE_RATIO = float(
        get_pwms_config("benchmarks", "minimum_daily_coverage_ratio", 0.5)
    )

    @classmethod
    def _ticker(cls, benchmark):
        return cls.TICKERS[benchmark]

    @classmethod
    def _nifty_tri_series(cls, start, end=None, index_name="NIFTY 50"):
        """Fetch official Gross TRI history for an NSE index."""
        end = end or timezone.now().date()
        all_points = {}

        try:
            session = curl_requests.Session(impersonate="chrome")
        except Exception:
            session = requests.Session()

        def parse_response(response):
            response.raise_for_status()
            payload = response.json()
            raw_rows = payload.get("d", []) if isinstance(payload, dict) else payload
            rows = json.loads(raw_rows) if isinstance(raw_rows, str) else raw_rows
            if isinstance(rows, str):
                rows = json.loads(rows)
            if not isinstance(rows, list):
                raise ValueError(
                    "NSE Indices TRI endpoint returned an unexpected response shape"
                )
            return rows

        def request_rows(request_start, request_end):
            cinfo = (
                "{'name':'"+index_name+"',"
                f"'startDate':'{request_start.strftime('%d-%b-%Y')}',"
                f"'endDate':'{request_end.strftime('%d-%b-%Y')}',"
                "'indexName':'"+index_name+"'}"
            )
            payload = {"cinfo": cinfo}
            last_error = None

            for endpoint in cls.NIFTY_TRI_URLS:
                for body_mode in ("json", "data"):
                    try:
                        kwargs = {
                            "headers": cls.NIFTY_TRI_HEADERS,
                            "timeout": get_pwms_config(
                                "benchmarks",
                                "nifty_request_timeout_seconds",
                                60,
                            ),
                        }
                        if body_mode == "json":
                            kwargs["json"] = payload
                        else:
                            kwargs["data"] = json.dumps(payload)

                        response = session.post(endpoint, **kwargs)
                        rows = parse_response(response)
                        if rows:
                            return rows
                    except Exception as exc:
                        last_error = exc
            raise last_error or ValueError(
                "NSE Indices TRI endpoint returned no usable response"
            )

        try:
            session.get(
                get_pwms_config("benchmarks", "nifty_historical_page", ""),
                headers=cls.NIFTY_TRI_HEADERS,
                timeout=get_pwms_config("benchmarks", "nifty_bootstrap_timeout_seconds", 15),
            )

            # Prefer the complete requested history when the current endpoint
            # supports it. This prevents a date-filter failure from collapsing
            # every request to the latest available year.
            try:
                rows = request_rows(start, end)
                for row in rows:
                    point_date = cls._parse_date(row.get("Date"))
                    raw_value = row.get("TotalReturnsIndex")
                    if point_date is None or raw_value in (None, ""):
                        continue
                    try:
                        value = float(str(raw_value).replace(",", "").strip())
                    except (TypeError, ValueError):
                        continue
                    if value > 0 and start <= point_date <= end:
                        all_points[point_date.isoformat()] = {
                            "date": point_date.isoformat(),
                            "value": value,
                        }

                covered_start = min(all_points) if all_points else None
                covered_end = max(all_points) if all_points else None
                expected_rows = max(
                    cls.MINIMUM_HISTORY_ROWS,
                    int((end - start).days * cls.MINIMUM_DAILY_COVERAGE_RATIO),
                )
                if (
                    len(all_points) >= expected_rows
                    and covered_start
                    and date.fromisoformat(covered_start) <= start + timedelta(days=10)
                    and covered_end
                    and date.fromisoformat(covered_end) >= end - timedelta(days=10)
                ):
                    return [all_points[key] for key in sorted(all_points)]
            except Exception:
                all_points.clear()

            # Fallback for deployments enforcing a 365-day request limit.
            all_points.clear()
            cursor = start
            while cursor <= end:
                window_end = min(cursor + timedelta(days=364), end)
                rows = request_rows(cursor, window_end)

                window_points = 0
                for row in rows:
                    point_date = cls._parse_date(row.get("Date"))
                    raw_value = row.get("TotalReturnsIndex")
                    if point_date is None or raw_value in (None, ""):
                        continue
                    try:
                        value = float(str(raw_value).replace(",", "").strip())
                    except (TypeError, ValueError):
                        continue
                    if value > 0 and cursor <= point_date <= window_end:
                        all_points[point_date.isoformat()] = {
                            "date": point_date.isoformat(),
                            "value": value,
                        }
                        window_points += 1

                expected_window_rows = max(
                    cls.MINIMUM_HISTORY_ROWS,
                    int(
                        (window_end - cursor).days
                        * cls.MINIMUM_DAILY_COVERAGE_RATIO
                    ),
                )
                if (
                    window_points < expected_window_rows
                    and (window_end - cursor).days > 45
                ):
                    raise ValueError(
                        "NSE Indices TRI returned insufficient historical rows "
                        f"for {cursor.isoformat()} to {window_end.isoformat()}"
                    )
                cursor = window_end + timedelta(days=1)
        finally:
            session.close()

        return [all_points[key] for key in sorted(all_points)]

    @classmethod
    def ensure_benchmark_master_history(cls, start=None, end=None, force=False):
        """Ensure every configured NSE TRI and BSE 500 benchmark master exists locally."""
        end = end or timezone.now().date()
        start = start or (end - timedelta(days=cls.PERIOD_DAYS["5Y"] + 31))
        results = {}
        expected = max(
            cls.MINIMUM_HISTORY_ROWS,
            int((end - start).days * cls.MINIMUM_DAILY_COVERAGE_RATIO),
        )

        for benchmark in cls.SUPPORTED_BENCHMARKS:
            aggregate = BenchmarkMasterPoint.objects.filter(
                benchmark=benchmark,
                source__in=(
                    ["BSE500T_OFFICIAL", "BSE500T_FILE", "BSE500_TRI_PROXY_AMFI_NAV", "BSE500_TRI_PROXY"]
                    if benchmark == "BSE 500"
                    else ["MASTER"]
                ),
                date__gte=start,
                date__lte=end,
            ).aggregate(
                count=models.Count("id"),
                first_date=models.Min("date"),
                last_date=models.Max("date"),
            )
            count = int(aggregate["count"] or 0)
            first_date = aggregate["first_date"]
            last_date = aggregate["last_date"]
            coverage_ok = (
                first_date is not None
                and last_date is not None
                and first_date <= start + timedelta(days=10)
                and last_date >= end - timedelta(days=10)
            )
            if not force and count >= expected and coverage_ok:
                results[benchmark] = {
                    "downloaded": False,
                    "rows": count,
                    "first_date": first_date.isoformat(),
                    "last_date": last_date.isoformat(),
                }
                continue

            try:
                points = (
                    cls._nifty_tri_series(
                        start, end, index_name=cls.NIFTY_TRI_INDICES[benchmark]
                    )
                    if benchmark in cls.NIFTY_TRI_INDICES
                    else cls._bse_series(start)
                )
                saved = cls.save_benchmark_master(benchmark, points)
                results[benchmark] = {"downloaded": True, "rows": saved}
            except Exception as exc:
                # One unavailable provider must not prevent the other
                # benchmark from bootstrapping.
                results[benchmark] = {
                    "downloaded": False,
                    "rows": count,
                    "error": str(exc),
                }


        return results

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
                timeout=get_pwms_config("benchmarks", "bse_request_timeout_seconds", 45),
            )
            response.raise_for_status()
            return cls._load_bse_csv(response.text)

        response = requests.get(
            cls.BSE500_API,
            params={
                "strIndex": cls.TICKERS.get("BSE 500", "BSE500T"),
                "dtFromDate": start.strftime("%d/%m/%Y"),
                "dtToDate": end.strftime("%d/%m/%Y"),
                "period": "D",
            },
            headers=cls.BSE_HEADERS,
            timeout=get_pwms_config("benchmarks", "bse_request_timeout_seconds", 45),
        )
        response.raise_for_status()
        points = cls._parse_bse_api_csv(response.text)
        if not points:
            raise ValueError("BSE500T historical endpoint returned no observations")
        return points

    @classmethod
    def _has_sufficient_history(cls, points, start, end):
        """Reject partial provider responses before persisting benchmark history."""
        valid = [
            point for point in (points or [])
            if point.get("date") and point.get("value") is not None
            and float(point["value"]) > 0
            and start <= date.fromisoformat(point["date"]) <= end
        ]
        unique_dates = {point["date"] for point in valid}
        expected_rows = max(
            cls.MINIMUM_HISTORY_ROWS,
            int((end - start).days * cls.MINIMUM_DAILY_COVERAGE_RATIO),
        )
        if not unique_dates:
            return False
        first_date = date.fromisoformat(min(unique_dates))
        last_date = date.fromisoformat(max(unique_dates))
        return (
            len(unique_dates) >= expected_rows
            and first_date <= start + timedelta(days=10)
            and last_date >= end - timedelta(days=10)
        )

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
    def _amfi_bse_tri_proxy_series(cls, start, end):
        """Use the BSE 500 ETF's AMFI NAV history as a long-history TRI proxy.

        The ICICI Prudential BSE 500 ETF (AMFI scheme 143247) tracks BSE 500
        TRI and has NAV history from 2018. This is not the official index
        level: its NAV includes fund expenses and tracking difference.
        """
        scheme_code = cls.BSE_TRI_AMFI_SCHEME_CODE
        AMFIService.import_historical_master_navs(
            start,
            end,
            scheme_codes={scheme_code},
        )
        navs = (
            AMFIMasterNAV.objects
            .filter(
                scheme__scheme_code=scheme_code,
                source="AMFI",
                date__gte=start,
                date__lte=end,
            )
            .order_by("date")
            .values_list("date", "nav")
        )
        points = [
            {
                "date": nav_date.isoformat(),
                "value": float(nav),
                "source": "BSE500_TRI_PROXY_AMFI_NAV",
            }
            for nav_date, nav in navs
            if nav is not None and float(nav) > 0
        ]
        if not cls._has_sufficient_history(points, start, end):
            raise ValueError(
                "ICICI Prudential BSE 500 ETF AMFI NAV history is incomplete "
                f"for {start.isoformat()} to {end.isoformat()} "
                f"({len(points)} observations)"
            )
        return points

    @classmethod
    def _bse_series(cls, start):
        end = timezone.now().date()
        source_file = Path(cls.BSE500_FILE)
        # Only read the configured CSV when the path points to a regular file.
        if source_file.is_file():
            points = cls._load_bse_csv(
                source_file.read_text(encoding="utf-8-sig")
            )
            filtered = [
                point for point in points
                if start <= date.fromisoformat(point["date"]) <= end
            ]
            if cls._has_sufficient_history(filtered, start, end):
                return [dict(point, source="BSE500T_FILE") for point in filtered]

        # A short BSE response is not a usable history. The public BSE CSV
        # interface is an OHLC/price-index interface; accept BSE500T only when
        # the returned series covers the full requested range at daily density.
        bse_error = None
        try:
            points = cls._fetch_bse_history(start)
            if cls._has_sufficient_history(points, start, end):
                return [dict(point, source="BSE500T_OFFICIAL") for point in points]
            bse_error = ValueError(
                "BSE500T endpoint returned insufficient daily coverage "
                f"({len(points)} observations for {start.isoformat()} to {end.isoformat()})"
            )
        except Exception as exc:
            bse_error = exc

        # AMFI carries the ICICI Prudential BSE 500 ETF NAV history from
        # 2018 (scheme 143247). It tracks BSE 500 TRI and is a better long-
        # history proxy than exchange-listed ETF prices, which begin later.
        # NAV is still a fund proxy, not the official index level.
        proxy_errors = []
        try:
            points = cls._amfi_bse_tri_proxy_series(start, end)
            return points
        except Exception as exc:
            proxy_errors.append(f"AMFI ETF NAV: {exc}")

        # Last-resort listed ETF adjusted closes. They are proxies and are
        # accepted only when they cover the complete requested period.
        for ticker in cls.BSE_TRI_PROXY_TICKERS:
            try:
                points = cls._series(ticker, start)
                if not cls._has_sufficient_history(points, start, end):
                    proxy_errors.append(
                        f"{ticker}: insufficient daily coverage ({len(points)} observations)"
                    )
                    continue
                return [dict(point, source="BSE500_TRI_PROXY") for point in points]
            except Exception as exc:
                proxy_errors.append(f"{ticker}: {exc}")

        detail = "; ".join(proxy_errors) or "no configured TRI-tracking ETF proxy returned data"
        raise ValueError(
            "BSE 500 TRI history is unavailable. "
            f"BSE source: {bse_error}. ETF fallback: {detail}"
        ) from bse_error

    @classmethod
    def _benchmark_series(cls, benchmark, start, allow_source_refresh=False):
        """Read shared benchmark history and optionally bootstrap from its provider.

        Interactive comparison requests pass allow_source_refresh=False so a
        slow NSE/BSE request can never hold the chart API open. The application
        startup/daily benchmark worker is responsible for keeping this shared
        master current.
        """
        def read_master():
            allowed_sources = (
                ["BSE500T_OFFICIAL", "BSE500T_FILE", "BSE500_TRI_PROXY_AMFI_NAV", "BSE500_TRI_PROXY", "MASTER"]
                if benchmark == "BSE 500"
                else ["MASTER"]
            )
            stored = list(
                BenchmarkMasterPoint.objects
                .filter(
                    benchmark=benchmark,
                    source__in=allowed_sources,
                    date__gte=start,
                )
                .order_by("date", "id")
                .values("date", "value", "source")
            )
            boundary = (
                BenchmarkMasterPoint.objects
                .filter(
                    benchmark=benchmark,
                    source__in=allowed_sources,
                    date__lt=start,
                )
                .order_by("-date", "-id")
                .values("date", "value", "source")
                .first()
            )
            if boundary is not None:
                stored.insert(0, boundary)

            # Prefer official observations on duplicate dates; use the ETF
            # proxy only to fill gaps in the official series.
            by_date = {}
            for point in stored:
                if point["value"] is None or float(point["value"]) <= 0:
                    continue
                key = point["date"]
                current = by_date.get(key)
                source_priority = {
                    "BSE500T_OFFICIAL": 5,
                    "BSE500T_FILE": 4,
                    "BSE500_TRI_PROXY_AMFI_NAV": 3,
                    "BSE500_TRI_PROXY": 2,
                    "MASTER": 1,
                }
                if current is None or (
                    source_priority.get(point["source"], 0)
                    > source_priority.get(current["source"], 0)
                ):
                    by_date[key] = point

            return [
                {
                    "date": point["date"].isoformat(),
                    "value": float(point["value"]),
                    "source": point["source"],
                }
                for _, point in sorted(by_date.items())
            ]

        series = read_master()

        # A previously bootstrapped master can contain only a few sparse
        # observations (for example, older Nifty imports). The background
        # refresh rebuilds thin history so charts progressively gain coverage;
        # interactive reads never trigger that rebuild themselves.
        end = timezone.now().date()
        expected_rows = max(
            cls.MINIMUM_HISTORY_ROWS,
            int((end - start).days * cls.MINIMUM_DAILY_COVERAGE_RATIO),
        )
        first_date = date.fromisoformat(series[0]["date"]) if series else None
        last_date = date.fromisoformat(series[-1]["date"]) if series else None
        coverage_ok = (
            first_date is not None
            and last_date is not None
            and first_date <= start + timedelta(days=10)
            and last_date >= end - timedelta(days=10)
        )
        if len(series) >= expected_rows and coverage_ok:
            return series

        # Never perform external provider requests inside an interactive chart
        # request. Return any persisted observations immediately; the existing
        # background bootstrap/refresh fills missing index history separately.
        if not allow_source_refresh:
            return series

        try:
            points = (
                cls._nifty_tri_series(
                    start, end, index_name=cls.NIFTY_TRI_INDICES[benchmark]
                )
                if benchmark in cls.NIFTY_TRI_INDICES
                else cls._bse_series(start)
                if benchmark == "BSE 500"
                else []
            )
            if points:
                cls.save_benchmark_master(benchmark, points)
                # The fetched series is authoritative for this calculation.
                # Return it directly instead of immediately re-reading the
                # master with the original start filter; this preserves a
                # boundary observation needed to calculate a long-period
                # cumulative return when the latest provider observation is
                # slightly stale.
                return sorted(
                    (
                        point for point in points
                        if point.get("date") and point.get("value") is not None
                    ),
                    key=lambda point: point["date"],
                )
        except Exception:
            return series

        return read_master()

    @classmethod
    def save_benchmark_master(cls, benchmark, points):
        """Persist an externally fetched benchmark series as shared master data."""
        objects = [
            BenchmarkMasterPoint(
                benchmark=benchmark,
                date=date.fromisoformat(point["date"]),
                value=point["value"],
                source=point.get("source", "MASTER"),
            )
            for point in points
            if point.get("date") and point.get("value") is not None
        ]
        if objects:
            BenchmarkMasterPoint.objects.bulk_create(
                objects,
                batch_size=1000,
                update_conflicts=True,
                unique_fields=["benchmark", "date", "source"],
                update_fields=["value"],
            )
        return len(objects)

    @staticmethod
    def _period_return_detail(points, days, annualize_long_periods=True):
        if not points:
            return None

        points = sorted(points, key=lambda point: point["date"])
        end = points[-1]
        end_date = date.fromisoformat(end["date"])
        cutoff = end_date - timedelta(days=days)
        # Use the first available observation inside the requested window.
        # This avoids reaching backward beyond the requested period when the
        # exact cutoff date is a non-trading day or is otherwise unavailable.
        eligible = [
            point
            for point in points
            if date.fromisoformat(point["date"]) >= cutoff
        ]

        # If the requested window contains fewer than two observations,
        # retain the latest observation immediately before the cutoff.
        # This handles non-trading-day boundaries without changing the
        # existing preference for an observation inside the requested window.
        if len(eligible) < 2:
            prior = [
                point
                for point in points
                if date.fromisoformat(point["date"]) < cutoff
            ]
            if prior:
                eligible.insert(0, prior[-1])

        if not eligible:
            return None

        start = eligible[0]
        start_date = date.fromisoformat(start["date"])
        start_value = float(start["value"])
        end_value = float(end["value"])
        if start_value <= 0 or end_value <= 0:
            return None

        elapsed_days = max((end_date - start_date).days, 1)
        ratio = end_value / start_value
        if days > 365 and annualize_long_periods:
            return {
                "return": round((ratio ** (365.25 / elapsed_days) - 1.0) * 100.0, 10),
                "start_date": start["date"],
                "end_date": end["date"],
                "start_value": start_value,
                "end_value": end_value,
                "elapsed_days": elapsed_days,
                "method": "CAGR",
            }

        return {
            "return": round((ratio - 1.0) * 100.0, 10),
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
        history_start = timezone.now().date() - timedelta(
            days=cls.PERIOD_DAYS["5Y"] + 31
        )
        if (
            getattr(product, "product_type", None) == "MUTUAL_FUND"
            and cls._is_idcw_option(getattr(mutual_fund, "option", None))
        ):
            snapshots = list(
                PerformanceSnapshot.objects.filter(
                    product=product,
                    source="AMFI",
                    date__gte=history_start,
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
                    date__gte=history_start,
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
            PerformanceSnapshot.objects.filter(
                product=product,
                date__gte=history_start,
            ).order_by("-date", "-id")
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

    @staticmethod
    def _merge_chart_series(primary, secondary):
        """Merge two historical series, preferring primary values on duplicate dates.

        The shared AMFI master can be partially backfilled: it may contain a
        dense recent history while the older product-level AMFI snapshots still
        contain the observations needed for a full 1Y/3Y/5Y chart. A count-only
        choice between the two sources can therefore discard valid older
        history. Merge by date instead so the chart gets the complete coverage
        available from both sources without duplicating observations.
        """
        merged = {}

        for point in secondary or []:
            if not point.get("date") or point.get("value") is None:
                continue
            try:
                value = float(point["value"])
            except (TypeError, ValueError):
                continue
            if value > 0:
                merged[str(point["date"])] = {
                    "date": str(point["date"]),
                    "value": value,
                }

        for point in primary or []:
            if not point.get("date") or point.get("value") is None:
                continue
            try:
                value = float(point["value"])
            except (TypeError, ValueError):
                continue
            if value > 0:
                merged[str(point["date"])] = {
                    "date": str(point["date"]),
                    "value": value,
                }

        return [merged[key] for key in sorted(merged)]

    @classmethod
    def _fund_series(cls, product, start, end=None):
        """Return the historical fund series needed by the relative-performance chart.

        Mutual funds combine the shared AMFI master with the already-imported
        product-level AMFI history. This is intentionally a read-only operation:
        it never triggers an external AMFI download during chart rendering.

        The shared master is preferred when the same date exists in both
        sources, but older product-level observations are retained. This is
        important when the master has been backfilled only for a recent range:
        selecting the master by row count would otherwise truncate a 1Y/3Y/5Y
        chart even though the older NAV history is already available locally.
        """
        end = end or timezone.now().date()

        if getattr(product, "product_type", None) == "MUTUAL_FUND":
            scheme_code = cls._fund_scheme_code(product)
            if scheme_code:
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

                snapshot_values = (
                    PerformanceSnapshot.objects.filter(
                        product=product,
                        source="AMFI",
                        date__gte=start,
                        date__lte=end,
                    )
                    .exclude(nav_or_value__isnull=True)
                    .order_by("date", "id")
                    .values_list("date", "nav_or_value")
                )
                snapshot_series = [
                    {
                        "date": snapshot_date.isoformat(),
                        "value": float(nav),
                    }
                    for snapshot_date, nav in snapshot_values
                    if nav and float(nav) > 0
                ]

                merged_series = cls._merge_chart_series(
                    master_series,
                    snapshot_series,
                )
                if len(merged_series) >= 2:
                    return merged_series

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
                .values_list("date", "nav_or_value")
            )
            return [
                {
                    "date": snapshot_date.isoformat(),
                    "value": float(nav),
                }
                for snapshot_date, nav in snapshots
                if nav and float(nav) > 0
            ]

        snapshots = (
            PerformanceSnapshot.objects.filter(
                product=product,
                date__gte=start,
                date__lte=end,
            )
            .exclude(nav_or_value__isnull=True)
            .order_by("date", "id")
            .values_list("date", "nav_or_value")
        )
        return [
            {"date": snapshot_date.isoformat(), "value": float(nav)}
            for snapshot_date, nav in snapshots
            if nav and float(nav) > 0
        ]

    @staticmethod
    def _product_benchmark(product):
        if not product:
            return None
        selected = getattr(product, "comparison_benchmark", None)
        if selected:
            return "BSE 500" if selected == "BSE 500 TRI" else selected
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
        if benchmark not in cls.SUPPORTED_BENCHMARKS:
            return {
                "benchmark": benchmark,
                "available": False,
                "message": "Unsupported benchmark.",
            }

        max_days = cls.PERIOD_DAYS["5Y"] + 31
        start = timezone.now().date() - timedelta(days=max_days)
        try:
            series = cls._benchmark_series(
                benchmark, start, allow_source_refresh=False
            )
        except Exception:
            series = []

        if len(series) < 2:
            return {
                "benchmark": benchmark,
                "available": False,
                "benchmark_metrics": {
                    period: None for period in cls.PERIOD_DAYS
                },
                "message": (
                    "Stored benchmark history is not ready yet. "
                    "The background benchmark refresh will populate it."
                ),
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
    def _aligned_chart_series(product_points, benchmark_points, days):
        """Build actual values keyed by product valuation dates.

        Match benchmark observations exactly by date when possible; otherwise
        use the latest benchmark observation on or before the product date.
        Future benchmark observations are never used.
        """
        product = sorted(
            (
                point for point in product_points
                if point.get("date") and point.get("value") is not None
                and float(point["value"]) > 0
            ),
            key=lambda point: point["date"],
        )
        benchmark = sorted(
            (
                point for point in benchmark_points
                if point.get("date") and point.get("value") is not None
                and float(point["value"]) > 0
            ),
            key=lambda point: point["date"],
        )
        if not product or not benchmark:
            return None

        product_end = date.fromisoformat(product[-1]["date"])
        requested_start = product_end - timedelta(days=days)
        product_window = [
            point for point in product
            if requested_start <= date.fromisoformat(point["date"]) <= product_end
        ]
        if not product_window:
            return None

        benchmark_index = 0
        latest_benchmark = None
        aligned = []
        for product_point in product_window:
            product_date = date.fromisoformat(product_point["date"])
            while benchmark_index < len(benchmark):
                candidate = benchmark[benchmark_index]
                if date.fromisoformat(candidate["date"]) > product_date:
                    break
                latest_benchmark = candidate
                benchmark_index += 1
            if latest_benchmark is not None:
                aligned.append({
                    "date": product_point["date"],
                    "product_value": float(product_point["value"]),
                    "benchmark_value": float(latest_benchmark["value"]),
                })

        if len(aligned) < 2:
            return None
        return {
            "points": aligned,
            "start_date": aligned[0]["date"],
            "end_date": aligned[-1]["date"],
        }

    @staticmethod
    def _weekly_chart_observations(points):
        """Reduce aligned daily observations to the last available trading date per week.

        The supplied observations are already aligned on fund valuation dates.
        Grouping uses ISO calendar weeks and preserves the actual observed value;
        no interpolation, averaging, or forward filling is performed.
        """
        weekly = {}
        for point in points or []:
            point_date = date.fromisoformat(point["date"])
            iso_year, iso_week, _ = point_date.isocalendar()
            key = (iso_year, iso_week)
            current = weekly.get(key)
            if current is None or point_date > date.fromisoformat(current["date"]):
                weekly[key] = point

        return [
            weekly[key]
            for key in sorted(weekly)
        ]

    @classmethod
    def calculate(cls, product, chart_period="1Y"):
        benchmark = cls._product_benchmark(product)
        if not benchmark or benchmark not in cls.SUPPORTED_BENCHMARKS:
            return None

        max_days = cls.PERIOD_DAYS["5Y"] + 31
        start = timezone.now().date() - timedelta(days=max_days)
        try:
            benchmark_series = cls._benchmark_series(
                benchmark, start, allow_source_refresh=False
            )
        except Exception:
            benchmark_series = []

        if len(benchmark_series) < 2:
            return {
                "benchmark": benchmark,
                "available": False,
                "message": (
                    "Stored benchmark history is not ready yet. "
                    "The background benchmark refresh will populate it."
                ),
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
        differences = {}
        for period in cls.PERIOD_DAYS:
            fund_return = fund_metrics.get(period)
            benchmark_return = benchmark_metrics.get(period)
            differences[period] = (
                fund_return - benchmark_return
                if fund_return is not None and benchmark_return is not None
                else None
            )
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

        history_start = timezone.now().date() - timedelta(
            days=cls.PERIOD_DAYS["5Y"] + 31
        )
        product_chart = cls._fund_series(
            product,
            history_start,
            end=timezone.now().date(),
        )

        # Benchmark history is shared master data. Do not restrict the chart
        # to whatever benchmark subset happened to be returned by an earlier
        # product-specific bootstrap. Use the requested chart range and let
        # _aligned_chart_series trim it to the product valuation dates.
        benchmark_chart = [
            point
            for point in benchmark_series
            if date.fromisoformat(point["date"]) <= timezone.now().date()
        ]
        aligned_chart = cls._aligned_chart_series(
            product_chart,
            benchmark_chart,
            chart_days,
        )
        weekly_chart_points = (
            cls._weekly_chart_observations(aligned_chart["points"])
            if aligned_chart
            else []
        )

        # Normalize both investments from the same first weekly observation.
        # Keep actual NAV/index values alongside the indexed ₹100 series for
        # chart tooltips. Return tables and their calculations remain untouched.
        if len(weekly_chart_points) >= 2:
            fund_base = weekly_chart_points[0]["product_value"]
            benchmark_base = weekly_chart_points[0]["benchmark_value"]
            indexed_chart_points = [
                {
                    "date": point["date"],
                    "product_value": point["product_value"],
                    "benchmark_value": point["benchmark_value"],
                    "product_indexed": round(
                        (point["product_value"] / fund_base) * 100.0, 6
                    ),
                    "benchmark_indexed": round(
                        (point["benchmark_value"] / benchmark_base) * 100.0, 6
                    ),
                }
                for point in weekly_chart_points
            ]
        else:
            indexed_chart_points = []

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
            "benchmark_data_source": (
                "ICICI Prudential BSE 500 ETF AMFI NAV proxy (tracks BSE 500 TRI; fund expenses and tracking difference apply)"
                if benchmark == "BSE 500"
                and any(point.get("source") == "BSE500_TRI_PROXY_AMFI_NAV" for point in benchmark_series)
                else "BSE 500 TRI-tracking ETF adjusted-close proxy (tracking error may apply)"
                if benchmark == "BSE 500"
                and any(point.get("source") == "BSE500_TRI_PROXY" for point in benchmark_series)
                else "Official BSE 500 TRI history"
                if benchmark == "BSE 500"
                and any(point.get("source") in {"BSE500T_OFFICIAL", "BSE500T_FILE"} for point in benchmark_series)
                else "Stored BSE benchmark history (legacy source provenance unavailable)"
                if benchmark == "BSE 500"
                else f"Official {benchmark} TRI history"
            ),
            "differences": differences,
            "comparison": comparison,
            "chart_period": chart_period,
            "chart": (
                {
                    "frequency": "weekly",
                    "investment_amount": 100,
                    "fund": [
                        {"date": point["date"], "value": point["product_value"]}
                        for point in indexed_chart_points
                    ],
                    "benchmark": [
                        {"date": point["date"], "value": point["benchmark_value"]}
                        for point in indexed_chart_points
                    ],
                    "aligned_points": weekly_chart_points,
                    "indexed_points": indexed_chart_points,
                    "start_date": indexed_chart_points[0]["date"],
                    "end_date": indexed_chart_points[-1]["date"],
                }
                if len(indexed_chart_points) >= 2
                else {
                    "fund": [],
                    "benchmark": [],
                    "aligned_points": [],
                    "start_date": None,
                    "end_date": None,
                }
            ),
        }