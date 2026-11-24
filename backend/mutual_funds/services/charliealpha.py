"""CharlieAlpha mutual-fund API adapter and database ingestion.

The adapter is intentionally opt-in: it is called by a management command or an
authenticated API request, never from AppConfig.ready(). Structured observations
are written to the existing global MF master-data tables; audit/extended provider
distributions are stored in MFSourceRun.metadata (database JSONField), not files.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import logging
import re
import threading
import time
from typing import Any
from urllib.parse import quote

import requests
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from mutual_funds.models import AMFIMasterNAV, AMFIMasterScheme, MFSchemeIdentityMap
from mutual_funds.master_models import (
    MFExternalPortfolioSnapshot,
    MFMasterData,
    MFMasterHolding,
    MFSourceRun,
)

logger = logging.getLogger(__name__)

BASE_URL = "https://api.charliealpha.in/v1"
SOURCE = "CHARLIEALPHA_API"
NAV_SOURCE = "CHARLIEALPHA"
MIN_REQUEST_INTERVAL = 0.205  # < 50 requests in every 10-second interval.
_REQUEST_LOCK = threading.Lock()
_LAST_REQUEST_AT = 0.0


def key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").casefold())


def text_value(value: Any) -> str:
    if value is None:
        return ""
    return " ".join(str(value).replace("\xa0", " ").strip().split())


def decimal_value(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    raw = text_value(value).replace(",", "").replace("%", "").replace("₹", "")
    if raw.casefold() in {"-", "na", "n/a", "nan", "none", "null"}:
        return None
    try:
        result = Decimal(raw)
    except (InvalidOperation, ValueError, TypeError):
        return None
    return result if result.is_finite() else None


def parse_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    raw = text_value(value)
    if not raw:
        return None
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%Y/%m/%d", "%d %b %Y", "%d-%b-%Y"):
        try:
            return datetime.strptime(raw[:20], fmt).date()
        except ValueError:
            continue
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        return None


def _first(record: Any, *names: str) -> Any:
    if not isinstance(record, dict):
        return None
    wanted = {key(name) for name in names}
    for name, value in record.items():
        if key(name) in wanted and value not in (None, ""):
            return value
    return None


def _records(payload: Any, preferred: tuple[str, ...] = ()) -> list[dict]:
    """Decode object arrays and the provider's fields + 2D-data table format."""
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if not isinstance(payload, dict):
        return []
    fields = payload.get("fields") or payload.get("columns") or payload.get("labels")
    for name in preferred + ("schemes", "funds", "results", "records", "rows", "items", "holdings", "periods", "benchmarks", "categories", "data"):
        value = payload.get(name)
        if not isinstance(value, list):
            continue
        if value and isinstance(value[0], dict):
            return [row for row in value if isinstance(row, dict)]
        if fields and value and isinstance(value[0], (list, tuple)):
            labels = [str(item) for item in fields]
            return [
                {labels[index]: item for index, item in enumerate(row) if index < len(labels)}
                for row in value if isinstance(row, (list, tuple))
            ]
    return []


def _json_safe(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _nested_values(node: Any):
    if isinstance(node, dict):
        for name, value in node.items():
            yield key(name), value
            yield from _nested_values(value)
    elif isinstance(node, list):
        for value in node:
            yield from _nested_values(value)


def _find_nested(node: Any, aliases: tuple[str, ...]) -> Any:
    wanted = {key(alias) for alias in aliases}
    if isinstance(node, dict):
        for name, value in node.items():
            if key(name) in wanted and value not in (None, ""):
                return value
        for value in node.values():
            found = _find_nested(value, aliases)
            if found not in (None, ""):
                return found
    elif isinstance(node, list):
        for value in node:
            found = _find_nested(value, aliases)
            if found not in (None, ""):
                return found
    return None


def _horizon_payload(payload: Any, horizon: str) -> dict:
    aliases = {horizon, horizon.upper(), horizon.replace("y", "yr"), horizon.replace("y", "year")}
    if isinstance(payload, dict):
        for name, value in payload.items():
            if key(name) in {key(item) for item in aliases} and isinstance(value, dict):
                return value
        for name in ("horizons", "metrics", "performance", "risk", "data"):
            child = payload.get(name)
            if isinstance(child, dict):
                result = _horizon_payload(child, horizon)
                if result:
                    return result
    return {}


def _scheme_identifier(row: dict) -> str:
    return text_value(_first(row, "scheme_code", "amfi_code", "amfi_scheme_code", "code", "schemeId", "scheme_id", "schemeCode"))


def _isin(row: dict) -> str:
    return text_value(_first(row, "isin", "isin_growth", "isin_code", "ISIN")).upper()


def _scheme_name(row: dict) -> str:
    return text_value(_first(row, "scheme_name", "name", "scheme", "fund_name", "fund"))


def _source_date(payload: Any) -> date:
    raw = _find_nested(payload, ("as_of", "as_of_date", "report_date", "date", "nav_date", "generated_at"))
    return parse_date(raw) or timezone.localdate()


class CharlieAlphaClient:
    """Polite, retrying HTTP client for CharlieAlpha's public GET-only API."""

    def __init__(self, *, timeout: tuple[int, int] = (5, 30), session=None):
        self.timeout = timeout
        self.session = session or requests.Session()
        self.session.headers.update({
            "Accept": "application/json",
            "User-Agent": "PWMS-MutualFund-Intelligence/1.0",
        })

    def get(self, endpoint: str, params: dict | None = None) -> Any:
        global _LAST_REQUEST_AT
        url = BASE_URL + "/" + endpoint.lstrip("/")
        last_error = None
        for attempt in range(5):
            with _REQUEST_LOCK:
                wait = MIN_REQUEST_INTERVAL - (time.monotonic() - _LAST_REQUEST_AT)
                if wait > 0:
                    time.sleep(wait)
                _LAST_REQUEST_AT = time.monotonic()
            try:
                response = self.session.get(url, params=params or {}, timeout=self.timeout)
                if response.status_code == 429 or response.status_code >= 500:
                    retry_after = response.headers.get("Retry-After", "")
                    try:
                        delay = min(30.0, max(0.0, float(retry_after)))
                    except (TypeError, ValueError):
                        delay = min(16.0, 0.5 * (2 ** attempt))
                    if attempt < 4:
                        time.sleep(delay)
                        continue
                response.raise_for_status()
                try:
                    return response.json()
                except ValueError as exc:
                    raise ValueError("CharlieAlpha returned a non-JSON success response for " + endpoint) from exc
            except (requests.Timeout, requests.ConnectionError, requests.HTTPError) as exc:
                last_error = exc
                if isinstance(exc, requests.HTTPError) and getattr(exc.response, "status_code", 0) < 500 and getattr(exc.response, "status_code", 0) != 429:
                    raise
                if attempt < 4:
                    time.sleep(min(16.0, 0.5 * (2 ** attempt)))
        raise RuntimeError("CharlieAlpha request failed after retries: " + url) from last_error


class CharlieAlphaService:
    """Translate CharlieAlpha payloads into existing PWMS global MF tables."""

    def __init__(self, client: CharlieAlphaClient | None = None, *, dry_run: bool = False):
        self.client = client or CharlieAlphaClient()
        self.dry_run = dry_run
        self.stats = {"fetched": 0, "inserted": 0, "updated": 0, "skipped": 0, "failed": 0, "unmatched": 0}
        self.errors: list[dict] = []

    def _run(self, command: str, *, as_of: date | None = None, metadata: dict | None = None, status: str | None = None):
        run = MFSourceRun.objects.create(
            source=SOURCE,
            command=command[:120],
            as_of_date=as_of or timezone.localdate(),
            source_url=BASE_URL + "/",
            status=status or (
                MFSourceRun.Status.DRY_RUN if self.dry_run
                else MFSourceRun.Status.PARTIAL if self.stats["failed"] or self.stats["unmatched"]
                else MFSourceRun.Status.SUCCESS
            ),
            completed_at=timezone.now(),
            rows_parsed=self.stats["fetched"],
            rows_inserted=0 if self.dry_run else self.stats["inserted"],
            rows_updated=0 if self.dry_run else self.stats["updated"],
            rows_skipped=self.stats["skipped"],
            rows_unmatched=self.stats["unmatched"],
            rows_failed=self.stats["failed"],
            errors=self.errors[:100],
            metadata=_json_safe(metadata or {}),
        )
        return run

    def _resolve_scheme(self, row: dict):
        code, isin, name = _scheme_identifier(row), _isin(row), _scheme_name(row)
        scheme = None
        if code:
            scheme = AMFIMasterScheme.objects.filter(scheme_code=code).first()
        if scheme is None and isin:
            candidates = AMFIMasterScheme.objects.filter(
                Q(isin_growth__iexact=isin) | Q(isin_dividend__iexact=isin)
            ).distinct()[:2]
            matches = list(candidates)
            scheme = matches[0] if len(matches) == 1 else None
            if len(matches) > 1:
                self.stats["unmatched"] += 1
                self.errors.append({"kind": "ambiguous_isin", "code": code, "isin": isin})
                return None
        if scheme is None and code:
            mapped = MFSchemeIdentityMap.objects.filter(
                source="CHARLIEALPHA", external_id=code, status=MFSchemeIdentityMap.STATUS_ACTIVE
            ).select_related("scheme").first()
            if mapped:
                scheme = mapped.scheme
        if scheme is None and name:
            matches = list(AMFIMasterScheme.objects.filter(scheme_name__iexact=name)[:2])
            scheme = matches[0] if len(matches) == 1 else None
            if len(matches) > 1:
                self.stats["unmatched"] += 1
                self.errors.append({"kind": "ambiguous_name", "code": code, "name": name})
                return None
        if scheme is None:
            self.stats["unmatched"] += 1
            if len(self.errors) < 100:
                self.errors.append({"kind": "unmatched_scheme", "code": code, "isin": isin, "name": name})
            return None
        if code and not self.dry_run:
            try:
                MFSchemeIdentityMap.objects.get_or_create(
                    source="CHARLIEALPHA", external_id=code, scheme=scheme,
                    defaults={
                        "external_name": name[:300],
                        "match_method": "ISIN_EXACT" if isin else "AMFI_CODE_OR_NAME_EXACT",
                        "status": MFSchemeIdentityMap.STATUS_ACTIVE,
                    },
                )
            except IntegrityError:
                self.stats["skipped"] += 1
        return scheme

    def _snapshot(self, scheme: AMFIMasterScheme, as_of: date) -> MFMasterData | None:
        if self.dry_run:
            return None
        snapshot, created = MFMasterData.objects.get_or_create(
            scheme=scheme,
            as_of_date=as_of,
            defaults={
                "scheme_code": scheme.scheme_code,
                "isin_growth": scheme.isin_growth or "",
                "isin_idcw": scheme.isin_dividend or "",
                "amc": scheme.amc_name or "",
                "scheme_name": scheme.scheme_name,
                "plan": scheme.plan or "",
                "option": scheme.option or "",
                "sebi_category_level_1": scheme.level_1_branch or "",
                "sebi_category_level_2": scheme.level_2_group or "",
                "sebi_category_level_3": scheme.level_3_category or "",
            },
        )
        self.stats["inserted" if created else "updated"] += int(created)
        return snapshot

    def _mark_source(self, snapshot: MFMasterData, field: str, endpoint: str, as_of: date):
        sources = dict(snapshot.field_sources or {})
        sources[field] = {
            "source": SOURCE,
            "source_url": BASE_URL + "/" + endpoint.lstrip("/"),
            "as_of_date": as_of.isoformat(),
            "fetched_at": timezone.now().isoformat(),
        }
        snapshot.field_sources = sources

    def sync_schemes(self, category: str = "") -> dict:
        cats_payload = self.client.get("mf/categories")
        cats = _records(cats_payload, ("categories", "data"))
        scheme_params = {"cat": category} if category else None
        schemes_payload = self.client.get("mf/schemes", scheme_params)
        schemes = _records(schemes_payload, ("schemes", "data", "results"))
        self.stats["fetched"] += len(schemes)
        mapped = 0
        unmatched = []
        for row in schemes:
            scheme = self._resolve_scheme(row)
            if scheme is None:
                unmatched.append({
                    "code": _scheme_identifier(row),
                    "isin": _isin(row),
                    "name": _scheme_name(row),
                    "category": _first(row, "category", "scheme_category"),
                })
                continue
            mapped += 1
            if self.dry_run:
                continue
            changed = []
            amc = text_value(_first(row, "amc", "amc_name", "fund_house", "fundhouse"))
            category_name = text_value(_first(row, "category", "scheme_category"))
            if amc and not scheme.amc_name:
                scheme.amc_name = amc[:200]
                changed.append("amc_name")
            if category_name and not scheme.level_3_category:
                scheme.level_3_category = category_name[:160]
                changed.append("level_3_category")
            if changed:
                scheme.save(update_fields=changed + ["updated_at"])
                self.stats["updated"] += 1
        run = self._run(
            "schemes",
            metadata={
                "category_count": len(cats),
                "categories": cats,
                "schemes_fetched": len(schemes),
                "schemes_mapped_to_amfi": mapped,
                "unmatched_schemes": unmatched[:500],
                "coverage_note": "CharlieAlpha scheme rows are linked only by exact AMFI code, exact ISIN or unique exact name.",
            },
        )
        return {"run_id": run.id, **self.stats, "schemes": len(schemes), "mapped": mapped, "unmatched": len(unmatched)}

    def _save_nav(self, scheme: AMFIMasterScheme, day: date, nav: Decimal, endpoint: str):
        if nav <= 0:
            self.stats["skipped"] += 1
            return
        if self.dry_run:
            return
        _, created = AMFIMasterNAV.objects.update_or_create(
            scheme=scheme, date=day, source=NAV_SOURCE, defaults={"nav": nav}
        )
        self.stats["inserted" if created else "updated"] += 1
        snapshot = self._snapshot(scheme, day)
        if snapshot is not None:
            snapshot.nav = nav
            snapshot.nav_as_of = day
            snapshot.nav_source = SOURCE
            self._mark_source(snapshot, "nav", endpoint, day)
            snapshot.save(update_fields=["nav", "nav_as_of", "nav_source", "field_sources", "updated_at"])

    def sync_navall(self) -> dict:
        payload = self.client.get("mf/lnavall")
        rows = _records(payload, ("data", "rows", "results"))
        self.stats["fetched"] += len(rows)
        for row in rows:
            scheme = self._resolve_scheme(row)
            day = parse_date(_first(row, "date", "nav_date"))
            nav = decimal_value(_first(row, "nav", "latest_nav", "value"))
            if not scheme or not day or nav is None:
                self.stats["skipped"] += 1
                continue
            try:
                with transaction.atomic():
                    self._save_nav(scheme, day, nav, "mf/lnavall")
            except Exception as exc:
                self.stats["failed"] += 1
                if len(self.errors) < 100:
                    self.errors.append({"kind": "nav_persist", "scheme_code": scheme.scheme_code, "error": str(exc)[:300]})
        run = self._run("navall", as_of=timezone.localdate(), metadata={"records": len(rows), "source": "compact lnavall; original AMFI NAV rows are not modified"})
        return {"run_id": run.id, **self.stats}

    def sync_nav_history(self, scheme_codes: list[str]) -> dict:
        for code in scheme_codes:
            payload = self.client.get("mf/hnav/" + quote(code, safe=""))
            dates = payload.get("dates", []) if isinstance(payload, dict) else []
            navs = payload.get("nav", []) if isinstance(payload, dict) else []
            if not isinstance(dates, list) or not isinstance(navs, list):
                dates, navs = [], []
            for raw_day, raw_nav in zip(dates, navs):
                day, nav = parse_date(raw_day), decimal_value(raw_nav)
                if not day or nav is None:
                    self.stats["skipped"] += 1
                    continue
                scheme = self._resolve_scheme({"scheme_code": code})
                if not scheme:
                    break
                self.stats["fetched"] += 1
                try:
                    with transaction.atomic():
                        self._save_nav(scheme, day, nav, "mf/hnav/" + code)
                except Exception as exc:
                    self.stats["failed"] += 1
                    self.errors.append({"kind": "nav_history", "code": code, "date": str(day), "error": str(exc)[:250]})
        run = self._run("nav_history", metadata={"schemes_requested": scheme_codes, "source": "daily NAV history"})
        return {"run_id": run.id, **self.stats}

    def _save_metric_record(self, row: dict, endpoint: str):
        scheme = self._resolve_scheme(row)
        if not scheme:
            return
        as_of = _source_date(row)
        raw_metrics = row.get("metrics", row)
        category_medians = row.get("category_medians") if isinstance(row, dict) else None
        horizons = {}
        for horizon in ("1y", "3y", "5y", "10y"):
            value = _horizon_payload(raw_metrics, horizon)
            if value:
                horizons[horizon] = value
        if not horizons and isinstance(raw_metrics, dict):
            horizons["unscoped"] = raw_metrics
        if self.dry_run:
            return
        snapshot = self._snapshot(scheme, as_of)
        if snapshot is None:
            return
        one = horizons.get("1y", {})
        three = horizons.get("3y", {})
        five = horizons.get("5y", {})
        ten = horizons.get("10y", {})
        def assign(field, value):
            number = decimal_value(value)
            if number is not None:
                setattr(snapshot, field, number)
        for horizon, fields in (
            (one, {"trailing_cagr": "return_1y_pct"}),
            (three, {"trailing_cagr": "return_3y_pct"}),
            (five, {"trailing_cagr": "return_5y_pct"}),
            (ten, {"trailing_cagr": "return_10y_pct"}),
        ):
            for alias, field in fields.items():
                assign(field, _first(horizon, alias, alias + "_pct", "cagr"))
        risk = three or one or five or ten or (next(iter(horizons.values())) if horizons else {})
        risk_fields = {
            "volatility": "std_dev_pct",
            "standard_deviation": "std_dev_pct",
            "sharpe": "sharpe",
            "sortino": "sortino",
            "beta": "beta",
            "alpha": "alpha_pct",
            "alpha_pct": "alpha_pct",
            "information_ratio": "information_ratio",
            "max_drawdown": "max_drawdown_pct",
            "max_drawdown_pct": "max_drawdown_pct",
            "upside_capture": "upside_capture_pct",
            "upside_capture_ratio": "upside_capture_pct",
            "downside_capture": "downside_capture_pct",
            "downside_capture_ratio": "downside_capture_pct",
            "tracking_error": "tracking_error_pct",
            "correlation": "r_squared",
        }
        for alias, field in risk_fields.items():
            value = _first(risk, alias, alias + "_pct")
            if value is not None:
                assign(field, value)
        rolling = _find_nested(three, ("rolling_cagr", "rolling_cagr_distribution", "rolling_returns"))
        if isinstance(rolling, dict):
            assign("rolling_3y_cagr_mean_pct", _first(rolling, "average", "avg", "mean"))
            assign("rolling_3y_cagr_min_pct", _first(rolling, "minimum", "min"))
            assign("rolling_3y_cagr_max_pct", _first(rolling, "maximum", "max"))
            observations = _first(rolling, "count", "sample_count", "observations", "n")
            try:
                snapshot.rolling_3y_cagr_observations = int(observations) if observations is not None else None
            except (TypeError, ValueError):
                pass
        self._mark_source(snapshot, "performance_and_risk", endpoint, as_of)
        snapshot.save()
        # Extended percentile/XIRR distributions and horizon-specific metrics
        # are retained as structured, parsed database observations, not raw files.
        self._run(
            "metrics",
            as_of=as_of,
            metadata={
                "scheme_code": scheme.scheme_code,
                "provider_scheme_code": _scheme_identifier(row),
                "scheme_name": scheme.scheme_name,
                "horizons": horizons,
                "category_medians": category_medians,
                "metrics_source": endpoint,
                "risk_metric_horizon_used_for_scalar_columns": "3y, falling back to 1y / 5y / 10y",
                "scalar_metric_mapping": risk_fields,
            },
        )

    def sync_metrics(self, scheme_codes: list[str] | None = None, category_slugs: list[str] | None = None) -> dict:
        if scheme_codes:
            for code in scheme_codes:
                payload = self.client.get("mf/metrics/" + quote(code, safe=""))
                self.stats["fetched"] += 1
                self._save_metric_record(payload if isinstance(payload, dict) else {}, "mf/metrics/" + code)
        else:
            categories = category_slugs
            if categories is None:
                categories = [
                    text_value(_first(row, "slug", "category_slug", "id"))
                    for row in _records(self.client.get("mf/categories"), ("categories", "data"))
                ]
                categories = [slug for slug in categories if slug]
            for slug in categories:
                payload = self.client.get("mf/metrics-by-cat/" + quote(slug, safe=""))
                rows = _records(payload, ("schemes", "funds", "results", "data"))
                self.stats["fetched"] += len(rows)
                for row in rows:
                    self._save_metric_record(row, "mf/metrics-by-cat/" + slug)
                medians = payload.get("category_medians") if isinstance(payload, dict) else None
                if medians:
                    self._run("category_metrics", metadata={"category_slug": slug, "category_medians": medians})
        return dict(self.stats)

    def _sync_series(self, domain: str, scheme_codes: list[str], limit: int | None = None) -> dict:
        if limit:
            scheme_codes = scheme_codes[:limit]
        for code in scheme_codes:
            endpoint = "mf/" + domain + "/" + quote(code, safe="")
            payload = self.client.get(endpoint)
            dates = payload.get("dates", []) if isinstance(payload, dict) else []
            values = payload.get(domain, []) if isinstance(payload, dict) else []
            if not isinstance(values, list):
                values = payload.get("values", []) if isinstance(payload, dict) else []
            rows = _records(payload, ("history", "records", "data"))
            if dates and values:
                observations = list(zip(dates, values))
            else:
                observations = [
                    (_first(row, "date", "as_of", "as_of_date", "period"), _first(row, domain, "value", "ter", "aum"))
                    for row in rows
                ]
            scheme = self._resolve_scheme({"scheme_code": code})
            if not scheme:
                continue
            for raw_day, raw_value in observations:
                day, value = parse_date(raw_day), decimal_value(raw_value)
                if not day or value is None:
                    self.stats["skipped"] += 1
                    continue
                self.stats["fetched"] += 1
                if self.dry_run:
                    continue
                snapshot = self._snapshot(scheme, day)
                if snapshot is None:
                    continue
                if domain == "ter":
                    if (scheme.plan or "").casefold() == "direct":
                        snapshot.ter_direct_pct = value
                    else:
                        snapshot.ter_regular_pct = value
                    snapshot.ter_as_of = day
                    snapshot.ter_source = SOURCE
                    self._mark_source(snapshot, "ter", endpoint, day)
                else:
                    snapshot.aum_cr = value
                    snapshot.aum_type = MFMasterData.AUM_QUARTERLY_AVG
                    snapshot.aum_as_of = day
                    snapshot.aum_source = SOURCE
                    self._mark_source(snapshot, "aum", endpoint, day)
                snapshot.save()
        run = self._run(domain + "_history", metadata={"schemes_requested": scheme_codes, "history_kind": "dated observations", "source": SOURCE})
        return {"run_id": run.id, **self.stats}

    def sync_ter(self, scheme_codes: list[str], limit: int | None = None) -> dict:
        return self._sync_series("ter", scheme_codes, limit)

    def sync_aum(self, scheme_codes: list[str], limit: int | None = None) -> dict:
        return self._sync_series("aum", scheme_codes, limit)

    def sync_holdings(self, scheme_codes: list[str], *, include_changes: bool = True, limit: int | None = None) -> dict:
        if limit:
            scheme_codes = scheme_codes[:limit]
        for code in scheme_codes:
            endpoint = "mf/holdings/" + quote(code, safe="")
            payload = self.client.get(endpoint, {"change": "yes"} if include_changes else None)
            scheme = self._resolve_scheme({"scheme_code": code})
            if not scheme:
                continue
            as_of = parse_date(_first(payload, "as_of", "as_of_date", "report_date")) or timezone.localdate()
            rows = _records(payload, ("holdings", "data", "results"))
            self.stats["fetched"] += len(rows)
            normalized = []
            sectors: dict[str, Decimal] = {}
            for row in rows:
                name = text_value(_first(row, "security_name", "name", "stock_name", "company", "instrument"))
                isin = _isin(row)
                weight = decimal_value(_first(row, "weight_pct", "pct_of_nav", "percentage_of_nav", "weight", "allocation_pct", "percentage"))
                if not name:
                    self.stats["skipped"] += 1
                    continue
                normalized.append({
                    "name": name,
                    "isin": isin,
                    "ticker": text_value(_first(row, "ticker", "symbol", "nse_symbol")),
                    "weight_pct": weight,
                    "quantity": decimal_value(_first(row, "quantity", "units")),
                    "market_value": decimal_value(_first(row, "market_value", "value")),
                    "asset_type": text_value(_first(row, "asset_type", "instrument_type", "security_type")),
                    "sector": text_value(_first(row, "sector")),
                    "industry": text_value(_first(row, "industry")),
                })
            if self.dry_run:
                continue
            date_basis = MFMasterHolding.DATE_BASIS_DISCLOSURE if _first(payload, "as_of", "as_of_date", "report_date") else MFMasterHolding.DATE_BASIS_FETCHED_AT
            persisted = []
            with transaction.atomic():
                for row in normalized:
                    identity = row["isin"] or re.sub(r"[^a-z0-9]", "", row["name"].casefold())
                    security_key = "CHARLIEALPHA:" + identity
                    holding, created = MFMasterHolding.objects.update_or_create(
                        scheme=scheme,
                        portfolio_date=as_of,
                        portfolio_date_basis=date_basis,
                        security_key=security_key,
                        defaults={
                            "security_name": row["name"][:300],
                            "isin": row["isin"][:20],
                            "ticker": row["ticker"][:40],
                            "instrument_type": row["asset_type"][:60] or "OTHER",
                            "sector": row["sector"][:150],
                            "industry": row["industry"][:150],
                            "quantity": row["quantity"],
                            "market_value": row["market_value"],
                            "pct_of_nav": row["weight_pct"],
                            "source": SOURCE,
                            "source_url": BASE_URL + "/" + endpoint,
                            "fetched_at": timezone.now(),
                        },
                    )
                    self.stats["inserted" if created else "updated"] += 1
                    persisted.append(holding)
                    if row["sector"] and row["weight_pct"] is not None:
                        sectors[row["sector"]] = sectors.get(row["sector"], Decimal("0")) + row["weight_pct"]
                snapshot = self._snapshot(scheme, as_of)
                if snapshot is not None:
                    top = sorted(normalized, key=lambda item: item["weight_pct"] or Decimal("-1"), reverse=True)[:10]
                    snapshot.top10_holdings = [
                        {"name": item["name"], "isin": item["isin"], "weight_pct": str(item["weight_pct"]) if item["weight_pct"] is not None else None}
                        for item in top
                    ]
                    snapshot.sector_allocation = {name: str(value) for name, value in sectors.items()}
                    snapshot.holdings_as_of = as_of
                    snapshot.holdings_source = SOURCE
                    self._mark_source(snapshot, "holdings", endpoint, as_of)
                    snapshot.save()
                total_weight = sum((item["weight_pct"] or Decimal("0") for item in normalized), Decimal("0"))
                ext = MFExternalPortfolioSnapshot.objects.filter(
                    scheme=scheme, provider="CHARLIEALPHA", provider_as_of_date=as_of
                ).order_by("-observed_at").first()
                values = {
                    "provider": "CHARLIEALPHA",
                    "provider_as_of_date": as_of,
                    "scheme_name": scheme.scheme_name,
                    "fund_house": scheme.amc_name or "",
                    "category": scheme.level_3_category or "",
                    "holdings_count": len(normalized),
                    "total_weight_pct": total_weight,
                    "quality_flags": ["WEIGHT_COVERAGE_INCOMPLETE"] if any(item["weight_pct"] is None for item in normalized) else [],
                    "source_url": BASE_URL + "/" + endpoint,
                }
                if ext:
                    for field, value in values.items():
                        setattr(ext, field, value)
                    ext.observed_at = timezone.now()
                    ext.save()
                else:
                    MFExternalPortfolioSnapshot.objects.create(scheme=scheme, **values)
            # Store provider-reported changes in the run audit table, separate from the latest holdings.
            change_data = _first(payload, "changes", "holding_changes", "change_summary")
            self._run("holdings_changes", as_of=as_of, metadata={
                "scheme_code": scheme.scheme_code,
                "provider_scheme_code": code,
                "report_date": as_of.isoformat(),
                "holdings_count": len(normalized),
                "provider_changes": change_data,
            })
        return dict(self.stats)

    def sync_benchmarks(self, *, limit: int | None = None) -> dict:
        payload = self.client.get("benchmark/list")
        benchmarks = _records(payload, ("benchmarks", "data", "results"))
        self.stats["fetched"] += len(benchmarks)
        if limit:
            benchmarks = benchmarks[:limit]
        for benchmark in benchmarks:
            slug = text_value(_first(benchmark, "slug", "benchmark_slug", "code"))
            if not slug:
                self.stats["skipped"] += 1
                continue
            history = self.client.get("benchmark/hnav/" + quote(slug, safe=""))
            dates = history.get("dates", []) if isinstance(history, dict) else []
            values = history.get("nav", []) if isinstance(history, dict) else []
            if not isinstance(dates, list) or not isinstance(values, list):
                dates, values = [], []
            series = [
                {"date": parse_date(day).isoformat(), "normalized_value": str(decimal_value(value))}
                for day, value in zip(dates, values)
                if parse_date(day) is not None and decimal_value(value) is not None
            ]
            self.stats["fetched"] += len(series)
            if not self.dry_run:
                self._run("benchmark_history", metadata={
                    "slug": slug,
                    "name": text_value(_first(benchmark, "name", "index_name", "benchmark_name")),
                    "category": text_value(_first(benchmark, "category", "asset_class")),
                    "normalization": {
                        "is_normalized": True,
                        "base_value": 100.0,
                        "base_date": series[0]["date"] if series else None,
                        "warning": "Provider history is rebased to 100 at the start of each requested window; it is not an absolute official TRI series.",
                    },
                    "series": series,
                })
        run = self._run("benchmarks", metadata={"benchmarks_listed": len(benchmarks), "normalization_policy": "Normalized values are not inserted into MFBenchmarkTRI."})
        return {"run_id": run.id, **self.stats, "benchmarks": len(benchmarks)}

    def compare(self, scheme_codes: list[str], *, overlap_only: bool = False) -> dict:
        if not 2 <= len(scheme_codes) <= 20:
            raise ValueError("Select between 2 and 20 mutual-fund scheme codes.")
        endpoint = "mf/overlap" if overlap_only else "mf/compare"
        payload = self.client.get(endpoint, {"codes": ",".join(scheme_codes), "include_pairwise": "true"})
        run = self._run("overlap" if overlap_only else "compare", metadata={
            "scheme_codes": scheme_codes,
            "provider_result": payload,
            "provider_partial_result": bool(_first(payload, "partial", "is_partial")),
            "report_date_warning": _first(payload, "warning", "report_date_warning"),
        })
        return {"run_id": run.id, "result": payload}
