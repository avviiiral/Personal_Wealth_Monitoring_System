"""Ingest CharlieAlpha NPS, SIF, GIFT City and instrument endpoint families.

These products have identifiers and reporting semantics distinct from AMFI
mutual-fund schemes. Their responses are stored as versioned JSON snapshots in
MFProviderResourceSnapshot; they are never linked to AMFIMasterScheme.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any
from types import SimpleNamespace
from urllib.parse import quote, urlencode

from django.utils import timezone

from mutual_funds.master_models import MFProviderResourceSnapshot, MFSourceRun
from mutual_funds.services.charliealpha import (
    BASE_URL,
    SOURCE,
    CharlieAlphaClient,
    _find_nested,
    _first,
    _json_safe,
    _records,
    decimal_value,
    parse_date,
    text_value,
)


def _resource_records(payload: Any) -> list[dict]:
    """Normalize documented object arrays and compact fields/labels + data tables."""
    rows = _records(payload, ("sifs", "schemes", "funds", "results", "records", "rows", "items", "data"))
    if rows:
        return _json_safe(rows)
    if isinstance(payload, dict):
        dates = payload.get("dates")
        values = payload.get("nav")
        if isinstance(dates, list) and isinstance(values, list):
            return [
                {"date": day, "nav": value}
                for day, value in zip(dates, values)
            ]
        # GIFT NAV-history responses are keyed by plan variant. Latest-NAV
        # can also return one scalar NAV object per plan, so preserve both forms.
        plan_rows = []
        for plan, series in payload.items():
            if not isinstance(series, dict):
                continue
            dates = series.get("dates")
            values = series.get("nav")
            if isinstance(dates, list) and isinstance(values, list):
                plan_rows.extend(
                    {"plan": str(plan), "date": day, "nav": value}
                    for day, value in zip(dates, values)
                )
                continue
            if _first(series, "nav", "latest_nav", "value") is not None:
                plan_rows.append({"plan": str(plan), **series})
        if plan_rows:
            return _json_safe(plan_rows)
    return []


def _identifier(row: dict, family: str) -> str:
    if family == "NPS":
        value = _first(row, "scheme_code", "nps_scheme_code", "code", "scheme_id", "schemeId", "id")
    elif family == "SIF":
        value = _first(row, "scheme_code", "sif_scheme_code", "scheme_id", "sif_id", "code", "id", "isin")
    else:
        value = _first(row, "fund_id", "gift_fund_id", "fund_code", "scheme_id", "code", "id", "isin")
    return text_value(value)


class CharlieAlphaExtendedService:
    """Endpoint-specific sync for CharlieAlpha non-mutual-fund product families."""

    def __init__(self, *, client: CharlieAlphaClient | None = None, dry_run: bool = False):
        self.client = client or CharlieAlphaClient()
        self.dry_run = dry_run
        self.stats = {"requests": 0, "snapshots_saved": 0, "records_normalized": 0, "failed": 0}

    def _store(
        self,
        family: str,
        endpoint: str,
        *,
        identifier: str = "",
        params: dict | None = None,
        payload: Any = None,
        error: str = "",
    ) -> MFProviderResourceSnapshot:
        params = params or {}
        safe_payload = _json_safe(payload if payload is not None else {})
        records = _resource_records(safe_payload) if not error else []
        record_count = len(records)
        if not record_count and isinstance(safe_payload, dict):
            row_arrays = (
                "data", "schemes", "funds", "results", "records", "rows",
                "items", "holdings", "periods", "benchmarks", "categories",
                "dates", "nav",
            )
            declares_rows = any(isinstance(safe_payload.get(name), list) for name in row_arrays)
            # Metrics/detail objects count as one resource; explicitly empty
            # tables and histories remain zero rows rather than fake records.
            record_count = 1 if safe_payload and not declares_rows else 0
        raw_date = _find_nested(
            safe_payload,
            ("as_of", "as_of_date", "date", "nav_date", "latest_as_of", "generated_at", "report_date", "ex_date"),
        )
        if raw_date is None and isinstance(safe_payload, dict):
            date_series = safe_payload.get("dates")
            if isinstance(date_series, list) and date_series:
                raw_date = date_series[-1]
            else:
                for plan_data in safe_payload.values():
                    if isinstance(plan_data, dict) and isinstance(plan_data.get("dates"), list) and plan_data["dates"]:
                        raw_date = plan_data["dates"][-1]
                        break
        data_date = parse_date(raw_date)
        query = urlencode({key: value for key, value in params.items() if value not in (None, "")}, doseq=True)
        source_url = BASE_URL + "/" + endpoint.lstrip("/") + (("?" + query) if query else "")
        canonical = json.dumps(safe_payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        self.stats["records_normalized"] += len(records)
        if error:
            self.stats["failed"] += 1
        if self.dry_run:
            return SimpleNamespace(pk=None, records=records, record_count=record_count, success=not bool(error))
        snapshot = MFProviderResourceSnapshot.objects.create(
            family=family,
            endpoint=endpoint,
            identifier=text_value(identifier),
            request_params=_json_safe(params),
            payload=safe_payload,
            records=records,
            record_count=record_count,
            as_of_date=data_date,
            source_url=source_url,
            payload_sha256=digest,
            success=not bool(error),
            error=error[:2000],
        )
        self.stats["snapshots_saved"] += 1
        command = ("provider_" + family.lower() + "_" + endpoint.replace("/", "_").strip("_"))[:120]
        MFSourceRun.objects.create(
            source=SOURCE,
            command=command,
            as_of_date=data_date or timezone.localdate(),
            source_url=source_url,
            status=MFSourceRun.Status.FAILED if error else MFSourceRun.Status.SUCCESS,
            started_at=snapshot.fetched_at,
            completed_at=snapshot.fetched_at,
            rows_parsed=len(records),
            rows_inserted=0 if error else 1,
            rows_failed=1 if error else 0,
            errors=[{"kind": "provider_request_failed", "error": error[:500]}] if error else [],
            metadata={
                "provider_family": family,
                "provider_endpoint": endpoint,
                "identifier": text_value(identifier),
                "request_params": _json_safe(params),
                "resource_snapshot_id": snapshot.pk,
                "record_count": record_count,
                "payload_sha256": digest,
            },
        )
        return snapshot

    def _get(
        self,
        family: str,
        endpoint: str,
        *,
        identifier: str = "",
        params: dict | None = None,
    ) -> MFProviderResourceSnapshot:
        self.stats["requests"] += 1
        try:
            payload = self.client.get(endpoint, params or None)
        except Exception as exc:
            return self._store(
                family,
                endpoint,
                identifier=identifier,
                params=params,
                error=str(exc),
            )
        return self._store(
            family,
            endpoint,
            identifier=identifier,
            params=params,
            payload=payload,
        )

    @staticmethod
    def _identifiers(snapshot: MFProviderResourceSnapshot, family: str) -> list[str]:
        values = []
        for row in snapshot.records:
            if isinstance(row, dict):
                value = _identifier(row, family)
                if value:
                    values.append(value)
        return list(dict.fromkeys(values))

    def sync_nps(self, *, limit: int = 0, horizon: str = "") -> dict:
        """Fetch NPS directory, then L-NAV, H-NAV and metrics for selected schemes."""
        directory = self._get("NPS", "nps/schemes")
        identifiers = self._identifiers(directory, "NPS")
        selected = identifiers[:limit] if limit else identifiers
        outcomes = [directory.pk]
        for code in selected:
            outcomes.append(self._get("NPS", "nps/lnav/" + quote(code, safe=""), identifier=code).pk)
            outcomes.append(self._get("NPS", "nps/hnav/" + quote(code, safe=""), identifier=code).pk)
            params = {"horizon": horizon} if horizon else None
            outcomes.append(self._get(
                "NPS", "nps/metrics/" + quote(code, safe=""),
                identifier=code, params=params,
            ).pk)
        return {
            "family": "NPS",
            "schemes_discovered": len(identifiers),
            "schemes_requested": len(selected),
            "snapshots_saved": len(outcomes),
            "snapshot_ids": outcomes,
        }

    def sync_sif(self, *, limit: int = 0, search_query: str = "") -> dict:
        """Fetch SIF directory and bulk NAV, then per-scheme NAV and history."""
        directory = self._get("SIF", "sif/schemes")
        bulk_nav = self._get("SIF", "sif/lnavall")
        search = None
        if text_value(search_query):
            search = self._get("SIF", "sif/search", params={"q": text_value(search_query)})
        identifiers = self._identifiers(directory, "SIF")
        selected = identifiers[:limit] if limit else identifiers
        ids = [directory.pk, bulk_nav.pk]
        if search:
            ids.append(search.pk)
        for code in selected:
            ids.append(self._get("SIF", "sif/lnav/" + quote(code, safe=""), identifier=code).pk)
            ids.append(self._get("SIF", "sif/hnav/" + quote(code, safe=""), identifier=code).pk)
        return {
            "family": "SIF",
            "schemes_discovered": len(identifiers),
            "schemes_requested": len(selected),
            "snapshots_saved": len(ids),
            "snapshot_ids": ids,
        }

    def sync_gift(
        self,
        *,
        limit: int = 0,
        search_query: str = "",
        plan: str = "",
        from_date: str = "",
        to_date: str = "",
    ) -> dict:
        """Fetch full GIFT directory, bulk NAV and selected fund/plan histories."""
        directory = self._get("GIFT", "gift/schemes", params={"compact": "false"})
        bulk_nav = self._get("GIFT", "gift/lnavall")
        search = None
        if text_value(search_query):
            search = self._get(
                "GIFT", "gift/search",
                params={"q": text_value(search_query), "compact": "false"},
            )
        identifiers = self._identifiers(directory, "GIFT")
        selected = identifiers[:limit] if limit else identifiers
        history_params = {}
        if plan:
            history_params["plan"] = text_value(plan)
        if from_date:
            start = parse_date(from_date)
            if not start:
                raise ValueError("GIFT history from_date must use YYYY-MM-DD.")
            history_params["start_date"] = start.isoformat()
        if to_date:
            end = parse_date(to_date)
            if not end:
                raise ValueError("GIFT history to_date must use YYYY-MM-DD.")
            history_params["end_date"] = end.isoformat()
        if history_params.get("start_date") and history_params.get("end_date"):
            if history_params["start_date"] > history_params["end_date"]:
                raise ValueError("GIFT history start_date cannot be later than end_date.")
        ids = [directory.pk, bulk_nav.pk]
        if search:
            ids.append(search.pk)
        for code in selected:
            ids.append(self._get("GIFT", "gift/lnav/" + quote(code, safe=""), identifier=code).pk)
            ids.append(self._get(
                "GIFT", "gift/hnav/" + quote(code, safe=""),
                identifier=code, params=history_params or None,
            ).pk)
        return {
            "family": "GIFT",
            "schemes_discovered": len(identifiers),
            "schemes_requested": len(selected),
            "snapshots_saved": len(ids),
            "snapshot_ids": ids,
        }

    def search_instruments(self, query: str, *, instrument_type: str = "", limit: int = 100) -> dict:
        query = text_value(query)
        if not query:
            raise ValueError("Instrument search requires a non-empty query.")
        if not 1 <= limit <= 100:
            raise ValueError("Instrument search limit must be between 1 and 100.")
        params = {"q": query, "limit": limit}
        if instrument_type:
            params["type"] = text_value(instrument_type)
        snapshot = self._get("INSTRUMENTS", "instruments/search", params=params)
        return {"family": "INSTRUMENTS", "snapshot_id": snapshot.pk, "records": snapshot.record_count}

    def instrument_details(self, isin: str) -> dict:
        isin = text_value(isin).upper()
        if not re.fullmatch(r"[A-Z]{2}[A-Z0-9]{9}[0-9]", isin):
            raise ValueError("Instrument details require a valid 12-character ISIN.")
        snapshot = self._get(
            "INSTRUMENTS",
            "instruments/" + quote(isin, safe=""),
            identifier=isin,
        )
        return {"family": "INSTRUMENTS", "snapshot_id": snapshot.pk, "records": snapshot.record_count}

    def corporate_actions(
        self,
        *,
        code: str = "",
        status_filter: str = "all",
        action_type: str = "ALL",
        from_date: str = "",
        to_date: str = "",
        limit: int = 50,
    ) -> dict:
        if status_filter not in {"all", "applied", "upcoming"}:
            raise ValueError("Corporate-action status must be all, applied or upcoming.")
        action_type = action_type.upper()
        if action_type not in {"ALL", "SPLIT", "BONUS"}:
            raise ValueError("Corporate-action type must be ALL, SPLIT or BONUS.")
        if not 1 <= limit <= 200:
            raise ValueError("Corporate-action limit must be between 1 and 200.")
        params = {"status": status_filter, "action_type": action_type, "limit": limit}
        if code:
            params["code"] = text_value(code)
        if bool(from_date) != bool(to_date):
            raise ValueError("Corporate-action from_date and to_date must be supplied together.")
        if from_date and to_date:
            start, end = parse_date(from_date), parse_date(to_date)
            if not start or not end:
                raise ValueError("Corporate-action dates must use YYYY-MM-DD.")
            if start > end:
                raise ValueError("Corporate-action from_date cannot be later than to_date.")
            params["from_date"] = start.isoformat()
            params["to_date"] = end.isoformat()
        snapshot = self._get(
            "CORPORATE_ACTIONS",
            "instruments/corp-act",
            identifier=text_value(code),
            params=params,
        )
        return {
            "family": "CORPORATE_ACTIONS",
            "snapshot_id": snapshot.pk,
            "records": snapshot.record_count,
            "success": snapshot.success,
        }
