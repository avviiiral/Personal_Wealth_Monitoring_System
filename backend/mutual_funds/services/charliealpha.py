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
from urllib.parse import quote, urlencode

import requests
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from mutual_funds.models import AMFIMasterNAV, AMFIMasterScheme
from mutual_funds.master_models import (
    MFSchemeIdentityMap,
    MFExternalPortfolioSnapshot,
    MFMasterData,
    MFProviderBenchmark,
    MFProviderBenchmarkObservation,
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
        if isinstance(value, dict):
            nested = _records(value, preferred)
            if nested:
                return nested
            continue
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
    aliases = ("scheme_code", "amfi_code", "amfi_scheme_code", "code", "schemeId", "scheme_id", "schemeCode")
    return text_value(_first(row, *aliases) or _find_nested(row, aliases))


def _isin(row: dict) -> str:
    aliases = ("isin", "isin_growth", "isin_code", "ISIN")
    return text_value(_first(row, *aliases) or _find_nested(row, aliases)).upper()


def _scheme_name(row: dict) -> str:
    aliases = ("scheme_name", "name", "scheme", "fund_name", "fund")
    return text_value(_first(row, *aliases) or _find_nested(row, aliases))


def _source_date(payload: Any) -> date:
    raw = _find_nested(payload, ("as_of", "as_of_date", "report_date", "date", "nav_date", "generated_at"))
    return parse_date(raw) or timezone.localdate()


def _identifier_record(identifier: Any) -> dict:
    """Build the identity shape expected by the strict AMFI/ISIN resolver."""
    value = text_value(identifier)
    if len(value) == 12 and not value.isdigit():
        return {"isin": value.upper()}
    return {"scheme_code": value}


def _category_records(payload: Any) -> list[dict]:
    """Flatten the provider's asset-class grouped category response without losing its raw shape."""
    rows: list[dict] = []
    seen: set[str] = set()
    wrappers = {"data", "categories", "results", "items", "groups"}

    def visit(node: Any, asset_class: str = "") -> None:
        if isinstance(node, list):
            for item in node:
                if isinstance(item, str):
                    slug = text_value(item)
                    if slug and slug.casefold() not in seen:
                        seen.add(slug.casefold())
                        rows.append({"slug": slug, "name": slug, "asset_class": asset_class})
                else:
                    visit(item, asset_class)
            return
        if not isinstance(node, dict):
            return

        slug = text_value(_first(node, "slug", "category_slug"))
        if slug:
            normalized = slug.casefold()
            if normalized not in seen:
                seen.add(normalized)
                record = dict(node)
                record.setdefault("asset_class", text_value(_first(node, "asset_class", "group", "type")) or asset_class)
                rows.append(record)

        explicit_group = text_value(_first(node, "asset_class", "group", "asset_type"))
        for name, value in node.items():
            if isinstance(value, (list, dict)):
                child_group = explicit_group or (asset_class if name.casefold() in wrappers else text_value(name))
                visit(value, child_group)

    visit(payload)
    return rows


def _benchmark_window_params(
    range_name: str = "ALL",
    from_date: str | date | None = None,
    to_date: str | date | None = None,
) -> dict:
    """Validate the mutually exclusive relative-range or exact-date benchmark window."""
    allowed = {"ALL", "1M", "6M", "1Y", "3Y", "5Y", "10Y", "15Y", "20Y", "25Y"}
    has_from, has_to = from_date not in (None, ""), to_date not in (None, "")
    if has_from or has_to:
        if not (has_from and has_to):
            raise ValueError("Both benchmark from_date and to_date are required for a custom range.")
        raw_start, raw_end = text_value(from_date), text_value(to_date)
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw_start) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw_end):
            raise ValueError("Benchmark dates must use YYYY-MM-DD.")
        start, end = parse_date(raw_start), parse_date(raw_end)
        if not start or not end:
            raise ValueError("Benchmark dates must use YYYY-MM-DD.")
        if start > end:
            raise ValueError("Benchmark from_date cannot be later than to_date.")
        return {"from": start.isoformat(), "to": end.isoformat()}
    normalized = text_value(range_name or "ALL").upper()
    if normalized not in allowed:
        raise ValueError("Unsupported benchmark range. Use ALL, 1M, 6M, 1Y, 3Y, 5Y, 10Y, 15Y, 20Y or 25Y.")
    return {"range": normalized}


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
        self._last_run_stats = dict(self.stats)
        self._last_run_error_count = 0

    def _run(self, command: str, *, as_of: date | None = None, metadata: dict | None = None, status: str | None = None):
        # MFSourceRun rows are domain/operation audit records. Store per-run
        # deltas, not the cumulative counters for every preceding API call.
        run_stats = {
            key_name: self.stats[key_name] - self._last_run_stats.get(key_name, 0)
            for key_name in self.stats
        }
        run_errors = self.errors[self._last_run_error_count:]
        if status is None:
            if self.dry_run:
                run_status = MFSourceRun.Status.DRY_RUN
            elif run_stats["failed"] or run_stats["unmatched"] or run_stats["skipped"]:
                run_status = MFSourceRun.Status.PARTIAL
            else:
                run_status = MFSourceRun.Status.SUCCESS
        else:
            run_status = status
        run_metadata = dict(metadata or {})
        run_metadata["ingestion_stats"] = run_stats
        run = MFSourceRun.objects.create(
            source=SOURCE,
            command=command[:120],
            as_of_date=as_of or timezone.localdate(),
            source_url=BASE_URL + "/",
            status=run_status,
            completed_at=timezone.now(),
            rows_parsed=run_stats["fetched"],
            rows_inserted=0 if self.dry_run else run_stats["inserted"],
            rows_updated=0 if self.dry_run else run_stats["updated"],
            rows_skipped=run_stats["skipped"],
            rows_unmatched=run_stats["unmatched"],
            rows_failed=run_stats["failed"],
            errors=run_errors[:100],
            metadata=_json_safe(run_metadata),
        )
        self._last_run_stats = dict(self.stats)
        self._last_run_error_count = len(self.errors)
        return run

    def _resolve_scheme(self, row: dict):
        code, isin, name = _scheme_identifier(row), _isin(row), _scheme_name(row)
        scheme = None
        if code:
            scheme = AMFIMasterScheme.objects.filter(scheme_code=code).first()
        if scheme is not None and isin:
            known_isins = {
                value.upper() for value in (scheme.isin_growth, scheme.isin_dividend) if value
            }
            if known_isins and isin not in known_isins:
                self.stats["unmatched"] += 1
                self.errors.append({
                    "kind": "scheme_code_isin_conflict",
                    "code": code,
                    "isin": isin,
                    "amfi_scheme_code": scheme.scheme_code,
                    "known_isins": sorted(known_isins),
                })
                return None
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
            mapped_rows = list(
                MFSchemeIdentityMap.objects.filter(
                    source="CHARLIEALPHA",
                    external_id=code,
                    status=MFSchemeIdentityMap.STATUS_ACTIVE,
                ).select_related("scheme")[:2]
            )
            mapped_scheme_ids = {row.scheme_id for row in mapped_rows}
            if len(mapped_scheme_ids) > 1:
                self.stats["unmatched"] += 1
                if len(self.errors) < 100:
                    self.errors.append({
                        "kind": "ambiguous_provider_identifier",
                        "code": code,
                        "mapped_scheme_ids": sorted(mapped_scheme_ids),
                    })
                return None
            if mapped_rows:
                scheme = mapped_rows[0].scheme
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
        if code:
            existing_mappings = list(
                MFSchemeIdentityMap.objects.filter(
                    source="CHARLIEALPHA",
                    external_id=code,
                    status=MFSchemeIdentityMap.STATUS_ACTIVE,
                ).select_related("scheme")[:10]
            )
            conflicting_scheme_ids = sorted({
                row.scheme_id for row in existing_mappings if row.scheme_id != scheme.id
            })
            if conflicting_scheme_ids:
                self.stats["unmatched"] += 1
                if len(self.errors) < 100:
                    self.errors.append({
                        "kind": "provider_identifier_scheme_conflict",
                        "code": code,
                        "resolved_scheme_code": scheme.scheme_code,
                        "existing_scheme_ids": conflicting_scheme_ids,
                    })
                return None
            if not self.dry_run:
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

    def _resolve_identifier(self, identifier: str):
        """Resolve either a six-digit AMFI code, ISIN, or previously confirmed provider ID."""
        return self._resolve_scheme(_identifier_record(identifier))

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
        self.stats["inserted" if created else "updated"] += 1
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

    def sync_categories(self) -> dict:
        """Persist the complete grouped category catalogue and canonical slugs."""
        try:
            payload = self.client.get("mf/categories")
        except Exception as exc:
            self.stats["failed"] += 1
            error = {"kind": "categories_fetch", "endpoint": "mf/categories", "error": str(exc)[:300]}
            self.errors.append(error)
            run = self._run("categories", metadata={"error": str(exc)[:300], "source_endpoint": "mf/categories"})
            return {"run_id": run.id, "categories": 0, "slugs": 0, "failed": 1}
        categories = _category_records(payload)
        slugs = [text_value(_first(row, "slug", "category_slug")) for row in categories]
        self.stats["fetched"] += len(categories)
        run = self._run("categories", metadata={
            "category_count": len(categories),
            "category_slugs": list(dict.fromkeys(slug for slug in slugs if slug)),
            "category_rows": categories,
            "categories_payload": payload,
            "asset_classes": list(dict.fromkeys(
                text_value(_first(row, "asset_class", "group", "type"))
                for row in categories
                if text_value(_first(row, "asset_class", "group", "type"))
            )),
        })
        return {"run_id": run.id, "categories": len(categories), "slugs": len(set(slugs))}

    def sync_schemes(self, category: str = "") -> dict:
        categories_result = self.sync_categories()
        scheme_params = {"cat": category} if category else None
        try:
            schemes_payload = self.client.get("mf/schemes", scheme_params)
        except Exception as exc:
            self.stats["failed"] += 1
            error = {"kind": "schemes_fetch", "category": category or None, "error": str(exc)[:300]}
            self.errors.append(error)
            run = self._run("schemes", metadata={
                "category_slug": category or None,
                "categories_run_id": categories_result.get("run_id"),
                "request_params": scheme_params or {},
                "error": str(exc)[:300],
            })
            return {
                "run_id": run.id,
                "schemes": 0,
                "mapped": 0,
                "linked_scheme_codes": [],
                "unmatched": 0,
                "failed": 1,
            }
        schemes = _records(schemes_payload, ("schemes", "data", "results"))
        self.stats["fetched"] += len(schemes)
        mapped = 0
        mapped_scheme_codes: list[str] = []
        linked_provider_rows: list[dict] = []
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
            mapped_scheme_codes.append(scheme.scheme_code)
            linked_provider_rows.append({
                "scheme_code": scheme.scheme_code,
                "provider_identifier": _scheme_identifier(row),
                "provider_isin": _isin(row),
                "provider_response": row,
            })
            if self.dry_run:
                continue
            changed = []
            amc = text_value(_first(row, "amc", "amc_name", "fund_house", "fundhouse"))
            category_name = text_value(_first(row, "category", "scheme_category"))
            plan_name = text_value(_first(row, "plan", "plan_name"))
            option_name = text_value(_first(row, "option", "option_name"))
            if amc and not scheme.amc_name:
                scheme.amc_name = amc[:200]
                changed.append("amc_name")
            if category_name and not scheme.level_3_category:
                scheme.level_3_category = category_name[:160]
                changed.append("level_3_category")
            if plan_name and not scheme.plan:
                scheme.plan = plan_name[:40]
                changed.append("plan")
            if option_name and not scheme.option:
                scheme.option = option_name[:160]
                changed.append("option")
            if changed:
                scheme.save(update_fields=changed + ["updated_at"])
                self.stats["updated"] += 1
        mapped_scheme_codes = list(dict.fromkeys(mapped_scheme_codes))
        run = self._run(
            "schemes",
            metadata={
                "categories_run_id": categories_result["run_id"],
                "category_slug": category or None,
                "category_count": categories_result["categories"],
                "schemes_fetched": len(schemes),
                "schemes_mapped_to_amfi": mapped,
                "linked_scheme_codes": mapped_scheme_codes,
                "linked_provider_rows": linked_provider_rows,
                "unmatched_schemes": unmatched[:500],
                "provider_fields": schemes_payload.get("fields", []) if isinstance(schemes_payload, dict) else [],
                "coverage_note": "CharlieAlpha scheme rows are linked only by exact AMFI code, exact ISIN or unique exact name.",
            },
        )
        return {
            "run_id": run.id,
            **self.stats,
            "schemes": len(schemes),
            "mapped": mapped,
            "linked_scheme_codes": mapped_scheme_codes,
            "unmatched": len(unmatched),
        }

    def _save_nav(self, scheme: AMFIMasterScheme, day: date, nav: Decimal, endpoint: str, *, update_snapshot: bool = True):
        if nav <= 0:
            self.stats["skipped"] += 1
            return
        if self.dry_run:
            return
        _, created = AMFIMasterNAV.objects.update_or_create(
            scheme=scheme, date=day, source=NAV_SOURCE, defaults={"nav": nav}
        )
        self.stats["inserted" if created else "updated"] += 1
        if update_snapshot:
            snapshot = self._snapshot(scheme, day)
            if snapshot is not None:
                snapshot.nav = nav
                snapshot.nav_as_of = day
                snapshot.nav_source = SOURCE
                self._mark_source(snapshot, "nav", endpoint, day)
                snapshot.save(update_fields=["nav", "nav_as_of", "nav_source", "field_sources", "updated_at"])

    def sync_navall(self) -> dict:
        try:
            payload = self.client.get("mf/lnavall")
        except Exception as exc:
            self.stats["failed"] += 1
            self.errors.append({"kind": "navall_fetch", "endpoint": "mf/lnavall", "error": str(exc)[:300]})
            run = self._run("navall", metadata={"error": str(exc)[:300], "source_endpoint": "mf/lnavall"})
            return {"run_id": run.id, **self.stats}
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
        run = self._run("navall", as_of=timezone.localdate(), metadata={
            "records": len(rows),
            "provider_fields": payload.get("fields", []) if isinstance(payload, dict) else [],
            "source": "compact lnavall; original AMFI NAV rows are not modified",
        })
        return {"run_id": run.id, **self.stats}

    def sync_nav_history(self, scheme_codes: list[str]) -> dict:
        scheme_responses = []
        for code in scheme_codes:
            scheme = self._resolve_identifier(code)
            if not scheme:
                continue
            try:
                payload = self.client.get("mf/hnav/" + quote(code, safe=""))
            except Exception as exc:
                self.stats["failed"] += 1
                if len(self.errors) < 100:
                    self.errors.append({"kind": "nav_history_fetch", "code": code, "error": str(exc)[:250]})
                continue
            dates = payload.get("dates", []) if isinstance(payload, dict) else []
            navs = payload.get("nav", []) if isinstance(payload, dict) else []
            if not isinstance(dates, list) or not isinstance(navs, list):
                dates, navs = [], []
            scheme_responses.append({
                "scheme_code": scheme.scheme_code,
                "provider_identifier": code,
                "provider_response": payload,
                "date_count": len(dates),
                "nav_count": len(navs),
            })
            if len(dates) != len(navs):
                self.stats["failed"] += 1
                if len(self.errors) < 100:
                    self.errors.append({
                        "kind": "nav_history_array_length_mismatch",
                        "identifier": code,
                        "dates": len(dates),
                        "nav_values": len(navs),
                    })
            for raw_day, raw_nav in zip(dates, navs):
                day, nav = parse_date(raw_day), decimal_value(raw_nav)
                if not day or nav is None:
                    self.stats["skipped"] += 1
                    continue
                self.stats["fetched"] += 1
                try:
                    with transaction.atomic():
                        self._save_nav(scheme, day, nav, "mf/hnav/" + code, update_snapshot=False)
                except Exception as exc:
                    self.stats["failed"] += 1
                    self.errors.append({"kind": "nav_history", "code": code, "date": str(day), "error": str(exc)[:250]})
        run = self._run("nav_history", metadata={
            "schemes_requested": scheme_codes,
            "schemes": scheme_responses,
            "source": "daily NAV history",
        })
        return {"run_id": run.id, **self.stats}

    def _save_metric_record(self, row: dict, endpoint: str, identifier: str | None = None):
        identity_row = row if isinstance(row, dict) else {}
        if identifier:
            # Keep the request identifier as an additional exact identity signal
            # even when the provider response returns its own code/name fields.
            identity_row = {**_identifier_record(identifier), **identity_row}
        scheme = self._resolve_scheme(identity_row)
        if not scheme:
            return
        as_of = _source_date(row)
        raw_metrics = row.get("metrics") if isinstance(row, dict) else None
        if not isinstance(raw_metrics, dict):
            raw_metrics = _find_nested(row, ("metrics",))
        if not isinstance(raw_metrics, dict):
            raw_metrics = row
        category_medians = row.get("category_medians") if isinstance(row, dict) else None
        if category_medians is None:
            category_medians = _find_nested(row, ("category_medians",))
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
            "annualized_volatility": "std_dev_pct",
            "annualised_volatility": "std_dev_pct",
            "sharpe": "sharpe",
            "sharpe_ratio": "sharpe",
            "sortino": "sortino",
            "sortino_ratio": "sortino",
            "beta": "beta",
            "alpha": "alpha_pct",
            "alpha_pct": "alpha_pct",
            "jensen_alpha": "alpha_pct",
            "jensen_alpha_pct": "alpha_pct",
            "information_ratio": "information_ratio",
            "max_drawdown": "max_drawdown_pct",
            "max_drawdown_pct": "max_drawdown_pct",
            "upside_capture": "upside_capture_pct",
            "upside_capture_ratio": "upside_capture_pct",
            "downside_capture": "downside_capture_pct",
            "downside_capture_ratio": "downside_capture_pct",
            "tracking_error": "tracking_error_pct",
        }
        for alias, field in risk_fields.items():
            value = _first(risk, alias, alias + "_pct")
            if value is not None:
                assign(field, value)

        # Map only provider fields with a direct semantic equivalent in PWMS.
        # Pearson correlation is intentionally retained in the provider payload
        # and is never written to r_squared.
        scalar_fields = {
            "return_1m_pct": ("return_1m_pct", "return_1m"),
            "return_3m_pct": ("return_3m_pct", "return_3m"),
            "return_6m_pct": ("return_6m_pct", "return_6m"),
            "since_inception_cagr_pct": ("since_inception_cagr_pct", "inception_cagr"),
            "treynor": ("treynor", "treynor_ratio"),
            "risk_free_rate_used_pct": ("risk_free_rate_used_pct", "risk_free_rate_pct", "risk_free_rate"),
            "ratio_window_years": ("ratio_window_years", "window_years"),
            "pe": ("pe", "portfolio_pe", "price_to_earnings"),
            "pb": ("pb", "portfolio_pb", "price_to_book"),
            "dividend_yield_pct": ("dividend_yield_pct", "dividend_yield"),
            "turnover_ratio": ("turnover_ratio", "portfolio_turnover_ratio"),
            "large_cap_pct": ("large_cap_pct", "large_cap"),
            "mid_cap_pct": ("mid_cap_pct", "mid_cap"),
            "small_cap_pct": ("small_cap_pct", "small_cap"),
            "other_cap_pct": ("other_cap_pct", "other_cap"),
            "number_of_holdings": ("number_of_holdings", "holdings_count"),
            "top10_concentration_pct": ("top10_concentration_pct", "top_10_concentration_pct"),
            "ytm_pct": ("ytm_pct", "yield_to_maturity", "ytm"),
            "modified_duration": ("modified_duration",),
            "macaulay_duration": ("macaulay_duration",),
            "average_maturity": ("average_maturity",),
        }
        for field, aliases in scalar_fields.items():
            value = _find_nested(raw_metrics, aliases)
            if value is not None:
                assign(field, value)

        inception_date = parse_date(_find_nested(row, ("inception_date", "launch_date")))
        if inception_date is not None:
            snapshot.inception_date = inception_date
        credit_rating_split = _find_nested(raw_metrics, ("credit_rating_split", "rating_split", "credit_quality_split"))
        if isinstance(credit_rating_split, dict):
            snapshot.credit_rating_split = credit_rating_split

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
                "provider_response": row,
                "metrics_source": endpoint,
                "risk_metric_horizon_used_for_scalar_columns": "3y, falling back to 1y / 5y / 10y",
                "scalar_metric_mapping": risk_fields,
            },
        )

    def sync_metrics(
        self,
        scheme_codes: list[str] | None = None,
        category_slugs: list[str] | None = None,
        *,
        horizon: str | None = None,
    ) -> dict:
        normalized_horizon = text_value(horizon).lower() if horizon else ""
        if normalized_horizon and normalized_horizon not in {"1y", "3y", "5y", "10y"}:
            raise ValueError("Category metrics horizon must be one of 1y, 3y, 5y or 10y.")

        if scheme_codes:
            for identifier in scheme_codes:
                endpoint = "mf/metrics/" + quote(identifier, safe="")
                try:
                    payload = self.client.get(endpoint)
                except Exception as exc:
                    self.stats["failed"] += 1
                    if len(self.errors) < 100:
                        self.errors.append({"kind": "metrics_fetch", "identifier": identifier, "error": str(exc)[:250]})
                    continue
                self.stats["fetched"] += 1
                self._save_metric_record(
                    payload if isinstance(payload, dict) else {},
                    endpoint,
                    identifier=identifier,
                )
        else:
            categories = category_slugs
            if categories is None:
                category_run = MFSourceRun.objects.filter(
                    source=SOURCE, command="categories"
                ).order_by("-started_at").first()
                slugs = (category_run.metadata or {}).get("category_slugs", []) if category_run else []
                if not slugs:
                    self.sync_categories()
                    category_run = MFSourceRun.objects.filter(
                        source=SOURCE, command="categories"
                    ).order_by("-started_at").first()
                    slugs = (category_run.metadata or {}).get("category_slugs", []) if category_run else []
                categories = list(dict.fromkeys(text_value(slug) for slug in slugs if text_value(slug)))

            for slug in categories or []:
                endpoint = "mf/metrics-by-cat/" + quote(slug, safe="")
                params = {"horizon": normalized_horizon} if normalized_horizon else None
                source_endpoint = endpoint + (("?" + urlencode(params)) if params else "")
                try:
                    payload = self.client.get(endpoint, params)
                except Exception as exc:
                    self.stats["failed"] += 1
                    if len(self.errors) < 100:
                        self.errors.append({"kind": "category_metrics_fetch", "slug": slug, "horizon": normalized_horizon or None, "error": str(exc)[:250]})
                    continue
                rows = _records(payload, ("schemes", "funds", "results", "data"))
                self.stats["fetched"] += len(rows)
                for row in rows:
                    self._save_metric_record(row, source_endpoint)
                medians = payload.get("category_medians") if isinstance(payload, dict) else None
                self._run("category_metrics", metadata={
                    "category_slug": slug,
                    "provider_response": payload,
                    "horizon": normalized_horizon or None,
                    "category_medians": medians,
                    "metrics_count": len(rows),
                    "provider_response_status": "AVAILABLE" if rows or medians else "EMPTY",
                })
        return dict(self.stats)

    def _sync_series(self, domain: str, scheme_codes: list[str], limit: int | None = None) -> dict:
        if limit:
            scheme_codes = scheme_codes[:limit]
        scheme_responses = []
        for code in scheme_codes:
            endpoint = "mf/" + domain + "/" + quote(code, safe="")
            try:
                payload = self.client.get(endpoint)
            except Exception as exc:
                self.stats["failed"] += 1
                if len(self.errors) < 100:
                    self.errors.append({"kind": domain + "_fetch", "code": code, "error": str(exc)[:250]})
                continue
            dates = payload.get("dates", []) if isinstance(payload, dict) else []
            values = payload.get(domain, []) if isinstance(payload, dict) else []
            if not isinstance(values, list):
                aliases = ("values", "ter", "ter_pct") if domain == "ter" else ("values", "aum_cr", "average_aum")
                values = next((payload.get(alias) for alias in aliases if isinstance(payload.get(alias), list)), []) if isinstance(payload, dict) else []
            rows = _records(payload, ("history", "records", "data"))
            if dates and values:
                if len(dates) != len(values):
                    self.stats["failed"] += 1
                    if len(self.errors) < 100:
                        self.errors.append({
                            "kind": domain + "_array_length_mismatch",
                            "identifier": code,
                            "dates": len(dates),
                            "values": len(values),
                        })
                observations = list(zip(dates, values))
            else:
                value_aliases = (domain, "value", "ter", "ter_pct", "aum", "aum_cr", "average_aum")
                observations = [
                    (
                        _first(row, "date", "as_of", "as_of_date", "period"),
                        _first(row, *value_aliases),
                    )
                    for row in rows
                ]
            scheme = self._resolve_identifier(code)
            if not scheme:
                continue
            scheme_responses.append({
                "scheme_code": scheme.scheme_code,
                "provider_identifier": code,
                "observation_count": len(observations),
                "provider_response": payload,
            })
            for raw_day, raw_value in observations:
                day, value = parse_date(raw_day), decimal_value(raw_value)
                if not day or value is None or value < 0:
                    self.stats["skipped"] += 1
                    continue
                self.stats["fetched"] += 1
                if self.dry_run:
                    continue
                snapshot = self._snapshot(scheme, day)
                if snapshot is None:
                    continue
                if domain == "ter":
                    plan_name = (scheme.plan or "").casefold()
                    if plan_name.startswith("direct") or " direct" in plan_name:
                        snapshot.ter_direct_pct = value
                    elif plan_name.startswith("regular") or " regular" in plan_name:
                        snapshot.ter_regular_pct = value
                    else:
                        self.stats["skipped"] += 1
                        self.errors.append({
                            "kind": "ter_plan_unclassified",
                            "scheme_code": scheme.scheme_code,
                            "plan": scheme.plan,
                            "date": day.isoformat(),
                        })
                        continue
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
        run = self._run(domain + "_history", metadata={
            "schemes_requested": scheme_codes,
            "schemes": scheme_responses,
            "history_kind": "dated observations",
            "source": SOURCE,
            "endpoint_pattern": "mf/" + domain + "/{code}",
        })
        return {"run_id": run.id, **self.stats}

    def sync_ter(self, scheme_codes: list[str], limit: int | None = None) -> dict:
        return self._sync_series("ter", scheme_codes, limit)

    def sync_aum(self, scheme_codes: list[str], limit: int | None = None) -> dict:
        return self._sync_series("aum", scheme_codes, limit)

    def sync_holdings(self, scheme_codes: list[str], *, include_changes: bool = True, limit: int | None = None, report_date: str | None = None) -> dict:
        if limit:
            scheme_codes = scheme_codes[:limit]
        for code in scheme_codes:
            endpoint = "mf/holdings/" + quote(code, safe="")
            params = {"change": "yes"} if include_changes else {}
            if report_date:
                params["as_of"] = report_date
            source_endpoint = endpoint + (("?" + urlencode(params)) if params else "")
            try:
                payload = self.client.get(endpoint, params or None)
            except Exception as exc:
                self.stats["failed"] += 1
                if len(self.errors) < 100:
                    self.errors.append({"kind": "holdings_fetch", "code": code, "report_date": report_date, "error": str(exc)[:250]})
                continue
            scheme = self._resolve_identifier(code)
            if not scheme:
                continue
            as_of = parse_date(_first(payload, "as_of", "as_of_date", "report_date") or _find_nested(payload, ("as_of", "as_of_date", "report_date"))) or parse_date(report_date) or timezone.localdate()
            rows = _records(payload, ("holdings", "data", "results"))
            self.stats["fetched"] += len(rows)
            normalized = []
            sectors: dict[str, Decimal] = {}
            asset_weights: dict[str, Decimal] = {}
            for row in rows:
                name = text_value(_first(row, "security_name", "name", "stock_name", "company", "instrument"))
                isin = _isin(row)
                weight = decimal_value(_first(row, "weight_pct", "pct_of_nav", "percentage_of_nav", "weight", "allocation_pct", "percentage"))
                ticker = text_value(_first(row, "ticker", "symbol", "nse_symbol"))
                if not name:
                    name = isin or ticker
                if not name:
                    self.stats["skipped"] += 1
                    continue
                normalized.append({
                    "name": name,
                    "isin": isin,
                    "ticker": ticker,
                    "weight_pct": weight,
                    "quantity": decimal_value(_first(row, "quantity", "units")),
                    "market_value": decimal_value(_first(row, "market_value", "value")),
                    "asset_type": text_value(_first(row, "asset_type", "instrument_type", "security_type")),
                    "sector": text_value(_first(row, "sector")),
                    "industry": text_value(_first(row, "industry")),
                    "rating": text_value(_first(row, "rating", "credit_rating", "credit_quality")),
                    "rating_agency": text_value(_first(row, "rating_agency", "agency")),
                    "maturity_date": parse_date(_first(row, "maturity_date", "maturity")),
                    "ytm_pct": decimal_value(_first(row, "ytm_pct", "yield_to_maturity", "ytm")),
                })
            if not normalized:
                self.stats["skipped"] += 1
                self.stats["failed"] += 1
                self._run("holdings_unavailable", as_of=as_of, metadata={
                    "scheme_code": scheme.scheme_code,
                    "provider_scheme_code": code,
                    "data_status": "NOT_RETURNED_OR_NO_VALID_HOLDINGS",
                    "holdings_count_returned": len(rows),
                    "provider_response": payload,
                    "report_date": report_date,
                    "source_endpoint": source_endpoint,
                })
                continue
            if self.dry_run:
                continue
            date_basis = MFMasterHolding.DATE_BASIS_DISCLOSURE if (_first(payload, "as_of", "as_of_date", "report_date") or _find_nested(payload, ("as_of", "as_of_date", "report_date")) or report_date) else MFMasterHolding.DATE_BASIS_FETCHED_AT
            persisted = []
            with transaction.atomic():
                for row in normalized:
                    identity = row["isin"] or row["ticker"] or re.sub(r"[^a-z0-9]", "", row["name"].casefold())
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
                            "rating": row["rating"][:80],
                            "rating_agency": row["rating_agency"][:80],
                            "maturity_date": row["maturity_date"],
                            "ytm_pct": row["ytm_pct"],
                            "quantity": row["quantity"],
                            "market_value": row["market_value"],
                            "pct_of_nav": row["weight_pct"],
                            "source": SOURCE,
                            "source_url": source_endpoint,
                            "fetched_at": timezone.now(),
                        },
                    )
                    self.stats["inserted" if created else "updated"] += 1
                    persisted.append(holding)
                    if row["sector"] and row["weight_pct"] is not None:
                        sectors[row["sector"]] = sectors.get(row["sector"], Decimal("0")) + row["weight_pct"]
                    if row["asset_type"] and row["weight_pct"] is not None:
                        asset_type = row["asset_type"].casefold()
                        if "equity" in asset_type or "stock" in asset_type:
                            bucket = "equity"
                        elif any(token in asset_type for token in ("debt", "bond", "gsec", "treasury")):
                            bucket = "debt"
                        elif any(token in asset_type for token in ("cash", "liquid", "money market")):
                            bucket = "cash"
                        else:
                            bucket = "others"
                        asset_weights[bucket] = asset_weights.get(bucket, Decimal("0")) + row["weight_pct"]
                snapshot = self._snapshot(scheme, as_of)
                if snapshot is not None:
                    top = sorted(normalized, key=lambda item: item["weight_pct"] or Decimal("-1"), reverse=True)[:10]
                    snapshot.top10_holdings = [
                        {"name": item["name"], "isin": item["isin"], "weight_pct": str(item["weight_pct"]) if item["weight_pct"] is not None else None}
                        for item in top
                    ]
                    snapshot.sector_allocation = {name: str(value) for name, value in sectors.items()}
                    snapshot.equity_pct = asset_weights.get("equity")
                    snapshot.debt_pct = asset_weights.get("debt")
                    snapshot.cash_pct = asset_weights.get("cash")
                    snapshot.others_pct = asset_weights.get("others")
                    snapshot.holdings_as_of = as_of
                    snapshot.holdings_source = SOURCE
                    self._mark_source(snapshot, "holdings", source_endpoint, as_of)
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
                    "asset_allocation": {name: str(value) for name, value in asset_weights.items()},
                    "source_url": source_endpoint,
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
            self._run("holdings_changes" if include_changes else "holdings", as_of=as_of, metadata={
                "scheme_code": scheme.scheme_code,
                "provider_scheme_code": code,
                "report_date": as_of.isoformat(),
                "holdings_count": len(normalized),
                "provider_changes": change_data,
                "provider_response": payload,
                "normalized_holdings": normalized,
                "source_endpoint": source_endpoint,
                "asset_allocation": {name: str(value) for name, value in asset_weights.items()},
                "weight_coverage_pct": str(sum((item["weight_pct"] or Decimal("0") for item in normalized), Decimal("0"))),
            })
        return dict(self.stats)

    def sync_benchmarks(
        self,
        *,
        limit: int | None = None,
        range_name: str = "ALL",
        from_date: str | date | None = None,
        to_date: str | date | None = None,
    ) -> dict:
        """Store a provider-normalized benchmark window separately from official TRI levels."""
        window_params = _benchmark_window_params(range_name, from_date, to_date)
        try:
            payload = self.client.get("benchmark/list")
        except Exception as exc:
            self.stats["failed"] += 1
            self.errors.append({"kind": "benchmark_list_fetch", "endpoint": "benchmark/list", "error": str(exc)[:300]})
            run = self._run("benchmarks", metadata={
                "requested_window": window_params,
                "error": str(exc)[:300],
                "source_endpoint": "benchmark/list",
            })
            return {"run_id": run.id, **self.stats, "benchmarks": 0, "observations_persisted": 0}
        benchmarks = _records(payload, ("benchmarks", "data", "results"))
        self.stats["fetched"] += len(benchmarks)
        if limit:
            benchmarks = benchmarks[:limit]
        persisted = 0
        for benchmark in benchmarks:
            slug = text_value(_first(benchmark, "slug", "benchmark_slug", "code"))
            if not slug:
                self.stats["skipped"] += 1
                continue
            endpoint = "benchmark/hnav/" + quote(slug, safe="")
            try:
                history = self.client.get(endpoint, window_params)
            except Exception as exc:
                self.stats["failed"] += 1
                if len(self.errors) < 100:
                    self.errors.append({"kind": "benchmark_history_fetch", "slug": slug, "error": str(exc)[:250]})
                continue
            dates = history.get("dates", []) if isinstance(history, dict) else []
            values = history.get("nav", []) if isinstance(history, dict) else []
            if not isinstance(dates, list) or not isinstance(values, list):
                dates, values = [], []
            if len(dates) != len(values):
                self.stats["failed"] += 1
                if len(self.errors) < 100:
                    self.errors.append({
                        "kind": "benchmark_array_length_mismatch",
                        "slug": slug,
                        "dates": len(dates),
                        "values": len(values),
                    })
            observations = []
            for raw_day, raw_value in zip(dates, values):
                day, value = parse_date(raw_day), decimal_value(raw_value)
                if day is None or value is None:
                    self.stats["skipped"] += 1
                    continue
                observations.append((day, value))
            observations.sort(key=lambda item: item[0])
            self.stats["fetched"] += len(observations)
            name = text_value(_first(benchmark, "name", "index_name", "benchmark_name"))
            category = text_value(_first(benchmark, "category", "asset_class"))
            base_date = observations[0][0] if observations else None
            source_url = BASE_URL + "/" + endpoint + "?" + urlencode(window_params)
            if not self.dry_run:
                provider_benchmark, _ = MFProviderBenchmark.objects.update_or_create(
                    provider="CHARLIEALPHA",
                    slug=slug,
                    defaults={
                        "name": name,
                        "category": category,
                        "source_url": BASE_URL + "/benchmark/list",
                        "fetched_at": timezone.now(),
                    },
                )
                for day, value in observations:
                    _, created = MFProviderBenchmarkObservation.objects.update_or_create(
                        benchmark=provider_benchmark,
                        observation_date=day,
                        normalization_base_date=base_date,
                        defaults={
                            "normalized_value": value,
                            "normalization_method": "REBASED_TO_100",
                            "normalization_base_value": Decimal("100"),
                            "source": SOURCE,
                            "source_url": source_url,
                            "fetched_at": timezone.now(),
                        },
                    )
                    self.stats["inserted" if created else "updated"] += 1
                    persisted += 1
            self._run("benchmark_history", metadata={
                "slug": slug,
                "name": name,
                "category": category,
                "observation_count": len(observations),
                "start_date": observations[0][0].isoformat() if observations else None,
                "end_date": observations[-1][0].isoformat() if observations else None,
                "provider_response": history,
                "normalization": {
                    "is_normalized": True,
                    "method": "REBASED_TO_100",
                    "base_value": "100",
                    "base_date": base_date.isoformat() if base_date else None,
                    "warning": "Provider history is rebased to 100 at the start of the returned range; it is not an absolute official TRI series.",
                },
                "source_endpoint": endpoint,
                "requested_window": window_params,
                "provider_benchmark_id": provider_benchmark.id if not self.dry_run else None,
            })
        run = self._run("benchmarks", metadata={
            "benchmarks_listed": len(benchmarks),
            "observations_persisted": persisted,
            "normalization_policy": "Provider-normalized observations are stored separately and never inserted into MFBenchmarkTRI.",
            "requested_window": window_params,
            "provider_response": payload,
        })
        return {"run_id": run.id, **self.stats, "benchmarks": len(benchmarks), "observations_persisted": persisted}

    def compare(
        self,
        scheme_codes: list[str],
        *,
        overlap_only: bool = False,
        include_pairwise: bool | None = None,
    ) -> dict:
        normalized_codes = list(dict.fromkeys(
            (text_value(code).upper() if len(text_value(code)) == 12 else text_value(code))
            for code in scheme_codes if text_value(code)
        ))
        if not 2 <= len(normalized_codes) <= 20:
            raise ValueError("Select between 2 and 20 mutual-fund scheme codes or ISINs.")
        invalid_codes = [
            code for code in normalized_codes
            if not (re.fullmatch(r"\d{6}", code) or re.fullmatch(r"[A-Z]{2}[A-Z0-9]{9}\d", code))
        ]
        if invalid_codes:
            raise ValueError("Each comparison identifier must be a six-digit AMFI code or a 12-character ISIN: " + ", ".join(invalid_codes))
        endpoint = "mf/overlap" if overlap_only else "mf/compare"
        params = {"codes": ",".join(normalized_codes)}
        # Let CharlieAlpha apply its documented endpoint defaults unless the operator opts in.
        if include_pairwise is not None:
            params["include_pairwise"] = "true" if include_pairwise else "false"
        try:
            payload = self.client.get(endpoint, params)
        except Exception as exc:
            self.stats["failed"] += 1
            if len(self.errors) < 100:
                self.errors.append({"kind": "compare_fetch", "endpoint": endpoint, "codes": normalized_codes, "error": str(exc)[:300]})
            run = self._run("overlap" if overlap_only else "compare", metadata={
                "scheme_codes": normalized_codes,
                "endpoint": endpoint,
                "request_params": params,
                "error": str(exc)[:300],
            })
            return {"run_id": run.id, "result": None, "error": str(exc)[:300]}

        run = self._run("overlap" if overlap_only else "compare", metadata={
            "scheme_codes": normalized_codes,
            "endpoint": endpoint,
            "request_params": params,
            "provider_result": payload,
            "provider_partial_result": bool(_first(payload, "partial", "is_partial")) or bool(_find_nested(payload, ("excluded_schemes",))),
            "report_date_warning": _first(payload, "warning", "report_date_warning"),
        })
        return {"run_id": run.id, "result": payload}


    def search_schemes(self, query: str) -> dict:
        query = text_value(query)
        if not query:
            raise ValueError("A non-empty scheme search query is required.")
        try:
            payload = self.client.get("mf/search", {"q": query})
        except Exception as exc:
            self.stats["failed"] += 1
            if len(self.errors) < 100:
                self.errors.append({"kind": "search_fetch", "query": query, "error": str(exc)[:300]})
            run = self._run("search", metadata={"query": query, "error": str(exc)[:300]})
            return {"run_id": run.id, "count": 0, "results": [], "error": str(exc)[:300]}
        rows = _records(payload, ("results", "data", "schemes"))
        if not rows and isinstance(payload, dict) and isinstance(payload.get("data"), list):
            fields = payload.get("fields") or []
            rows = [
                {str(fields[i]): value for i, value in enumerate(row) if i < len(fields)}
                for row in payload["data"] if isinstance(row, (list, tuple))
            ]
        self.stats["fetched"] += len(rows)
        run = self._run("search", metadata={
            "query": query,
            "provider_fields": payload.get("fields", []) if isinstance(payload, dict) else [],
            "results": rows,
        })
        return {"run_id": run.id, "count": len(rows), "results": rows}

    def sync_holdings_periods(self, scheme_codes: list[str] | None = None) -> dict:
        """Persist available global or scheme-specific monthly disclosure periods."""
        if not scheme_codes:
            try:
                payload = self.client.get("mf/holdings-periods")
            except Exception as exc:
                self.stats["failed"] += 1
                self.errors.append({"kind": "holdings_periods_global_fetch", "endpoint": "mf/holdings-periods", "error": str(exc)[:300]})
                run = self._run("holdings_periods", metadata={"scope": "global", "error": str(exc)[:300]})
                return {"run_id": run.id, "periods": 0, **self.stats}
            periods = payload.get("periods", []) if isinstance(payload, dict) else []
            if not isinstance(periods, list):
                periods = []
            self.stats["fetched"] += len(periods)
            run = self._run("holdings_periods", metadata={
                "scope": "global",
                "generated_at": _find_nested(payload, ("generated_at",)),
                "latest_as_of": _find_nested(payload, ("latest_as_of",)),
                "periods": periods,
                "provider_response": payload,
            })
            return {"run_id": run.id, "periods": len(periods), **self.stats}

        results = []
        for code in scheme_codes:
            try:
                payload = self.client.get("mf/holdings-periods/" + quote(code, safe=""))
            except Exception as exc:
                self.stats["failed"] += 1
                if len(self.errors) < 100:
                    self.errors.append({"kind": "holdings_periods_fetch", "code": code, "error": str(exc)[:250]})
                continue
            periods = payload.get("periods", []) if isinstance(payload, dict) else []
            if not isinstance(periods, list):
                periods = []
            self.stats["fetched"] += len(periods)
            scheme = self._resolve_identifier(code)
            results.append({
                "scheme_code": scheme.scheme_code if scheme else code,
                "provider_identifier": code,
                "scheme_name": _find_nested(payload, ("scheme_name", "name")) or (scheme.scheme_name if scheme else ""),
                "latest_as_of": _find_nested(payload, ("latest_as_of",)),
                "periods": periods,
                "provider_response": payload,
            })
        run = self._run("holdings_periods", metadata={"scope": "scheme", "schemes": results})
        return {"run_id": run.id, "schemes": len(results), **self.stats}

    def sync_holdings_history(
        self,
        scheme_codes: list[str],
        *,
        period_limit: int = 4,
    ) -> dict:
        """Import dated disclosure snapshots; a limit protects against bulk overuse."""
        if period_limit < 0:
            raise ValueError("period_limit cannot be negative.")
        schemes_processed = 0
        snapshots_requested = 0
        period_registry = []
        for code in scheme_codes:
            try:
                periods_payload = self.client.get("mf/holdings-periods/" + quote(code, safe=""))
            except Exception as exc:
                self.stats["failed"] += 1
                if len(self.errors) < 100:
                    self.errors.append({"kind": "holdings_history_periods_fetch", "code": code, "error": str(exc)[:250]})
                continue
            periods = periods_payload.get("periods", []) if isinstance(periods_payload, dict) else []
            if not isinstance(periods, list):
                periods = []
            report_dates = []
            for item in periods:
                raw = item if isinstance(item, str) else _first(item, "as_of", "report_date", "date")
                parsed = parse_date(raw)
                if parsed and parsed not in report_dates:
                    report_dates.append(parsed)
            scheme = self._resolve_identifier(code)
            period_registry.append({
                "scheme_code": scheme.scheme_code if scheme else code,
                "provider_identifier": code,
                "scheme_name": _find_nested(periods_payload, ("scheme_name", "name")) or (scheme.scheme_name if scheme else ""),
                "latest_as_of": _find_nested(periods_payload, ("latest_as_of",)),
                "periods": periods,
                "provider_response": periods_payload,
            })
            report_dates.sort(reverse=True)
            if period_limit:
                report_dates = report_dates[:period_limit]
            schemes_processed += 1
            for as_of in report_dates:
                snapshots_requested += 1
                self.sync_holdings([code], include_changes=False, report_date=as_of.isoformat())
        if period_registry:
            self._run("holdings_periods", metadata={"scope": "scheme", "schemes": period_registry})
        return {
            "schemes_processed": schemes_processed,
            "historical_snapshots_requested": snapshots_requested,
            **self.stats,
        }

    def sync_fund_exposure(self, identifier: str, min_pct: Decimal | str | None = None) -> dict:
        """Persist stock exposure results for an ISIN, NSE symbol or BSE code."""
        identifier = text_value(identifier)
        if not identifier:
            raise ValueError("A security ISIN, NSE symbol or BSE scrip code is required.")
        params = {}
        if min_pct not in (None, ""):
            threshold = decimal_value(min_pct)
            if threshold is None or threshold < 0 or threshold > 100:
                raise ValueError("min_pct must be between 0 and 100.")
            params["min_pct"] = format(threshold.normalize(), "f") if threshold else "0"
        endpoint = "mf/fund-exposure/" + quote(identifier, safe="")
        try:
            payload = self.client.get(endpoint, params or None)
        except Exception as exc:
            self.stats["failed"] += 1
            if len(self.errors) < 100:
                self.errors.append({"kind": "fund_exposure_fetch", "identifier": identifier, "min_pct": params.get("min_pct"), "error": str(exc)[:300]})
            run = self._run("fund_exposure", metadata={
                "identifier": identifier,
                "min_pct": params.get("min_pct"),
                "source_endpoint": endpoint,
                "error": str(exc)[:300],
            })
            return {"run_id": run.id, "result": None, "error": str(exc)[:300]}

        run = self._run("fund_exposure", metadata={
            "identifier": identifier,
            "min_pct": params.get("min_pct"),
            "provider_result": payload,
            "report_date": _find_nested(payload, ("as_of", "report_date", "date")),
            "result_count": _find_nested(payload, ("count", "result_count")),
            "source_endpoint": endpoint,
        })
        return {"run_id": run.id, "result": payload}


    def sync_latest_nav_details(self, scheme_codes: list[str]) -> dict:
        """Capture per-scheme NAV movement and metadata absent from the compact bulk feed."""
        for code in scheme_codes:
            endpoint = "mf/lnav/" + quote(code, safe="")
            try:
                payload = self.client.get(endpoint)
            except Exception as exc:
                self.stats["failed"] += 1
                if len(self.errors) < 100:
                    self.errors.append({"kind": "latest_nav_detail_fetch", "code": code, "error": str(exc)[:250]})
                continue
            self.stats["fetched"] += 1
            scheme = self._resolve_scheme(payload if isinstance(payload, dict) else {}) or self._resolve_identifier(code)
            if not scheme:
                continue
            day = parse_date(_first(payload, "date", "nav_date", "as_of") or _find_nested(payload, ("date", "nav_date", "as_of")))
            nav = decimal_value(_first(payload, "nav", "latest_nav", "value") or _find_nested(payload, ("nav", "latest_nav", "value")))
            if day and nav is not None and nav > 0:
                self._save_nav(scheme, day, nav, endpoint)
            metadata = {
                "scheme_code": scheme.scheme_code,
                "provider_scheme_code": code,
                "nav_date": day.isoformat() if day else None,
                "nav": nav,
                "nav_change": decimal_value(_first(payload, "change", "nav_change", "change_value") or _find_nested(payload, ("change", "nav_change", "change_value"))),
                "nav_change_pct": decimal_value(_first(payload, "change_pct", "nav_change_pct", "change_percent", "change_percentage") or _find_nested(payload, ("change_pct", "nav_change_pct", "change_percent", "change_percentage"))),
                "amc": _first(payload, "amc", "amc_name", "fund_house") or _find_nested(payload, ("amc", "amc_name", "fund_house")),
                "category": _first(payload, "category", "scheme_category") or _find_nested(payload, ("category", "scheme_category")),
                "plan": _first(payload, "plan") or _find_nested(payload, ("plan",)),
                "option": _first(payload, "option") or _find_nested(payload, ("option",)),
                "ter": decimal_value(_first(payload, "ter", "expense_ratio", "ter_pct") or _find_nested(payload, ("ter", "expense_ratio", "ter_pct"))),
                "aum_cr": decimal_value(_first(payload, "aum", "aum_cr", "average_aum") or _find_nested(payload, ("aum", "aum_cr", "average_aum"))),
                "source_endpoint": endpoint,
                "provider_response": payload,
            }
            self._run("latest_nav_detail", as_of=day, metadata=metadata)
        return dict(self.stats)
