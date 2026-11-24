"""One function per masterdata pipeline job.

Each public job returns the MFSourceRun it logged. Network access is
injectable (``fetch_*`` / ``*_client`` arguments) so tests use saved fixtures.

Write rules shared by every job:
* idempotent upserts keyed on (scheme, date) - re-running never duplicates;
* a value is never overwritten with NULL because a source omitted it;
* each field group records its own *_as_of date and *_source;
* unchanged source content is skipped (MFSourceRun.source_file_hash).
"""
from __future__ import annotations

import io
import json
import logging
import os
import re
import sqlite3
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.management import call_command
from django.db import transaction
from django.db.models import Count, Max, Min
from django.utils import timezone

from config.database_scheduler_lock import DATABASE_SCHEDULER_LOCK
from mutual_funds.master_models import (
    MFAMFIRiskParameter, MFBenchmarkTRI, MFMasterData, MFMasterHolding,
    MFRiskFreeRate, MFSchemeIdentityMap,
)
from mutual_funds.models import AMFIMasterNAV, AMFIMasterScheme, AMFISchemeAAUM, MutualFundHolding
from mutual_funds.services import mf_ratios
from mutual_funds.services.masterdata import (
    normalize_key, parse_benchmark_tri_rows, persist_portfolio_rows,
    resolve_master_scheme, source_raw_directory,
)
from mutual_funds.services.sebi_mf_analytics import parse_amfi_navall_feed, persist_sebi_universe

from . import holdings_registry
from .http import PoliteHTTPClient, amfipy_client, with_retries
from .identity import IdentityResolver
from .parsers import (
    _date, parse_aaum_periods, parse_aaum_schemewise, parse_mfapi_payload,
    parse_risk_free_table, parse_risk_parameters, parse_ter_export,
    parse_tracking_error,
)
from .runlog import (
    JobResult, Status, content_hash, hash_already_processed, last_success_hash,
    record_file, run_job, sha256_bytes, successful_run_exists,
)

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")

NAVALL_URL = "https://www.amfiindia.com/spages/NAVAll.txt"
SCHEME_DATA_URL = "https://portal.amfiindia.com/DownloadSchemeData_Po.aspx?mf=0"
MFAPI_URL = "https://api.mfapi.in/mf/{code}"
TER_PAGE_URL = "https://www.amfiindia.com/ter-of-mf-schemes"
RISK_PAGE_URL = "https://www.amfiindia.com/risk-parameters"
AAUM_PAGE_URL = "https://www.amfiindia.com/aum-data/average-aum"
TRACKING_PAGE_URL = "https://www.amfiindia.com/tracking-error"
NAV_SOURCE_PRIORITY = {"AMFI_NAVALL": 4, "AMFI": 3, "mfapi.in": 2, "AMFI_HIST_ARCHIVE": 1}
INACTIVE_AFTER_DAYS = 7
DEFAULT_RISK_FREE_PCT = Decimal("6.5")  # same default as build_mf_masterdata
INBOX_SUFFIXES = {".csv", ".tsv", ".txt", ".xlsx", ".xls", ".xlsm", ".html", ".htm"}


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def ist_today() -> date:
    return timezone.now().astimezone(IST).date()


def data_dir(*parts) -> Path:
    return Path(settings.BASE_DIR, "data", *parts)


def _inbox_files(directory: Path):
    if not directory or not Path(directory).is_dir():
        return []
    return sorted(p for p in Path(directory).iterdir() if p.is_file() and p.suffix.casefold() in INBOX_SUFFIXES)


def _env_list(name, default):
    raw = os.environ.get(name, "")
    return tuple(item.strip() for item in raw.split(",") if item.strip()) or default


def _cache_download(source: str, filename: str, payload: bytes) -> None:
    try:
        path = source_raw_directory(source, ist_today()) / filename
        path.write_bytes(payload)
    except OSError:
        logger.warning("Could not cache raw %s payload.", source)


def fetch_navall_text() -> str:
    response = PoliteHTTPClient().get(NAVALL_URL, headers={"Accept": "text/plain"})
    text = response.text
    if "scheme code" not in text[:500].casefold():
        raise RuntimeError("AMFI NAVAll response did not contain the expected 'Scheme Code' header.")
    return text


def fetch_scheme_data_text() -> str:
    """AMFI scheme data (codes, ISINs, parent names); cached once per day."""
    cached = source_raw_directory("amfi_scheme_data", ist_today()) / "SchemeData.csv"
    if cached.is_file() and cached.stat().st_size > 0:
        return cached.read_text(encoding="utf-8", errors="replace")
    response = PoliteHTTPClient(timeout=120).get(SCHEME_DATA_URL, headers={"Accept": "text/csv"})
    text = response.content.decode("utf-8", errors="replace")
    if "scheme name" not in text[:300].casefold():
        raise RuntimeError("AMFI scheme data response is not the expected CSV.")
    try:
        cached.write_text(text, encoding="utf-8")
    except OSError:
        logger.warning("Could not cache AMFI scheme data at %s.", cached)
    return text


def _snapshot_identity(scheme) -> dict:
    return {
        "scheme_code": scheme.scheme_code, "isin_growth": scheme.isin_growth or "",
        "isin_idcw": scheme.isin_dividend or "", "amc": scheme.amc_name or "",
        "scheme_name": scheme.scheme_name, "plan": scheme.plan or "", "option": scheme.option or "",
        "sebi_category_level_1": scheme.level_1_branch or "",
        "sebi_category_level_2": scheme.level_2_group or "",
        "sebi_category_level_3": scheme.level_3_category or "",
    }


def apply_disclosure(scheme, disclosure_date, values, *, source, source_url="", flags=None):
    """Write disclosed fields onto the scheme's latest snapshot (or a new one).

    The latest snapshot on/after the disclosure date is used so that the next
    build_mf_masterdata run carries the values forward. None never overwrites.
    Returns True when a new snapshot row was created.
    """
    target = (
        MFMasterData.objects.filter(scheme=scheme, as_of_date__gte=disclosure_date)
        .order_by("-as_of_date").first()
    )
    created = False
    if target is None:
        target, created = MFMasterData.objects.get_or_create(
            scheme=scheme, as_of_date=disclosure_date, defaults=_snapshot_identity(scheme),
        )
    lineage = dict(target.field_sources or {})
    stamp = timezone.now().isoformat()
    changed = []
    for name, value in values.items():
        if value is None:
            continue
        setattr(target, name, value)
        changed.append(name)
        lineage[name] = {
            "source": source, "source_url": source_url or "",
            "as_of_date": disclosure_date.isoformat(), "fetched_at": stamp,
        }
    if not changed and not created:
        return False
    target.field_sources = lineage
    target.data_quality_flags = sorted(set(target.data_quality_flags or []) | set(flags or []))
    target.save()
    return created


def held_master_schemes():
    """AMFI master schemes that any family currently holds (ISIN first, code second)."""
    isins, codes = set(), set()
    for growth, dividend, code in MutualFundHolding.objects.filter(units__gt=0).values_list(
        "scheme__isin_growth", "scheme__isin_dividend", "scheme__scheme_code",
    ):
        isins.update(value.strip().upper() for value in (growth, dividend) if value and value.strip())
        if code and str(code).strip():
            codes.add(str(code).strip())
    if not isins and not codes:
        return AMFIMasterScheme.objects.none()
    by_isin = AMFIMasterScheme.objects.filter(isin_growth__in=isins) | AMFIMasterScheme.objects.filter(isin_dividend__in=isins)
    matched = set(by_isin.values_list("id", flat=True))
    matched |= set(AMFIMasterScheme.objects.filter(scheme_code__in=codes).values_list("id", flat=True))
    return AMFIMasterScheme.objects.filter(id__in=matched)


# --------------------------------------------------------------------------
# NAV + scheme master (NAVAll.txt)
# --------------------------------------------------------------------------
def mark_navall_inactive(today=None, grace_days=INACTIVE_AFTER_DAYS) -> dict:
    """Mark schemes missing from NAVAll for > grace_days inactive; never delete."""
    today = today or ist_today()
    deactivated = reactivated = 0
    for scheme in AMFIMasterScheme.objects.only(
        "id", "navall_active", "universe_last_seen_at", "amfi_source_date",
    ).iterator(chunk_size=2000):
        seen = scheme.universe_last_seen_at.astimezone(IST).date() if scheme.universe_last_seen_at else scheme.amfi_source_date
        if seen is None:
            continue
        missing = (today - seen).days > grace_days
        if missing and scheme.navall_active:
            AMFIMasterScheme.objects.filter(id=scheme.id).update(navall_active=False, navall_inactive_since=today)
            deactivated += 1
        elif not missing and not scheme.navall_active:
            AMFIMasterScheme.objects.filter(id=scheme.id).update(navall_active=True, navall_inactive_since=None)
            reactivated += 1
    return {"deactivated": deactivated, "reactivated": reactivated}


def sync_nav(fetch_text=None, today=None):
    def job(run):
        text = (fetch_text or fetch_navall_text)()
        digest = sha256_bytes(text.encode("utf-8"))
        if digest == last_success_hash("mf_sync_nav"):
            inactive = mark_navall_inactive(today)
            return JobResult.skip("UNCHANGED_FILE_HASH", source_hash=digest, source_url=NAVALL_URL, metadata=inactive)
        records = parse_amfi_navall_feed(text)
        if not records:
            raise ValueError("NAVAll contained no valid scheme/NAV rows; nothing was changed.")
        keys = {(row["scheme_code"], row["date"]) for row in records}
        dates = {row["date"] for row in records}
        with DATABASE_SCHEDULER_LOCK:
            before = set(
                AMFIMasterNAV.objects.filter(source="AMFI_NAVALL", date__in=dates)
                .values_list("scheme__scheme_code", "date")
            )
            summary = persist_sebi_universe(records)
            AMFIMasterScheme.objects.filter(
                scheme_code__in=[row["scheme_code"] for row in records], navall_active=False,
            ).update(navall_active=True, navall_inactive_since=None)
            inactive = mark_navall_inactive(today)
        _cache_download("amfi_navall", "NAVAll.txt", text.encode("utf-8"))
        inserted = len(keys - before)
        return JobResult(
            parsed=len(records), inserted=inserted, updated=len(keys) - inserted,
            source_hash=digest, source_url=NAVALL_URL, as_of_date=max(dates),
            metadata={**summary, **inactive},
        )

    return run_job("mf_sync_nav", "AMFI_NAVALL", job)


# --------------------------------------------------------------------------
# NAV history: mfapi.in gap fill, archive backfill, weekly gap check
# --------------------------------------------------------------------------
def fetch_mfapi_json(code, start=None, end=None):
    params = {}
    if start:
        params["startDate"] = start.isoformat()
    if end:
        params["endDate"] = end.isoformat()
    return PoliteHTTPClient(timeout=45).get(MFAPI_URL.format(code=code), params=params or None).json()


def _insert_missing_navs(scheme, observations, source) -> int:
    existing = set(AMFIMasterNAV.objects.filter(scheme=scheme).values_list("date", flat=True))
    rows = [
        AMFIMasterNAV(scheme=scheme, date=day, nav=value, source=source)
        for day, value in observations if day not in existing
    ]
    if rows:
        with DATABASE_SCHEDULER_LOCK, transaction.atomic():
            AMFIMasterNAV.objects.bulk_create(rows, batch_size=2000, ignore_conflicts=True)
    return len(rows)


def _scheme_isins(scheme) -> set:
    return {value.upper() for value in (scheme.isin_growth, scheme.isin_dividend) if value}


def _fill_from_mfapi(result, codes, fetch_json, start=None, end=None, max_requests=None):
    fetch_json = fetch_json or fetch_mfapi_json
    schemes = {s.scheme_code: s for s in AMFIMasterScheme.objects.filter(scheme_code__in=list(codes))}
    for index, code in enumerate(codes):
        if max_requests is not None and index >= max_requests:
            result.metadata["stopped_at_request_cap"] = max_requests
            break
        scheme = schemes.get(str(code))
        if scheme is None:
            result.unmatched += 1
            continue
        try:
            parsed = parse_mfapi_payload(fetch_json(scheme.scheme_code, start, end))
        except Exception as exc:
            result.add_error(scheme_code=code, error=exc)
            continue
        source_isins = {parsed["isin_growth"], parsed["isin_reinvestment"]} - {""}
        if source_isins and _scheme_isins(scheme) and not source_isins & _scheme_isins(scheme):
            result.add_error(scheme_code=code, error=f"ISIN mismatch {sorted(source_isins)}")
            continue
        result.parsed += len(parsed["navs"])
        result.inserted += _insert_missing_navs(scheme, parsed["navs"], "mfapi.in")
    if result.failed and result.status == Status.SUCCESS:
        result.status = Status.PARTIAL
    return result


def fill_nav_from_mfapi(scheme_codes=None, fetch_json=None, start=None, end=None, max_requests=None, job_name="mf_backfill_nav"):
    def job(run):
        codes = list(scheme_codes) if scheme_codes is not None else list(held_master_schemes().values_list("scheme_code", flat=True))
        result = JobResult(metadata={"source": "mfapi.in", "schemes_requested": len(codes)})
        return _fill_from_mfapi(result, codes, fetch_json, start, end, max_requests)

    return run_job(job_name, "MFAPI_NAV_HISTORY", job)


def _archive_table(con):
    names = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view')")}
    for candidate in ("nav", "funds"):
        if candidate in names:
            columns = {row[1] for row in con.execute(f'PRAGMA table_info("{candidate}")')}
            if {"scheme_code", "date", "nav"} <= columns:
                return candidate
    raise ValueError("Archive has no NAV table (expected 'nav' or 'funds' with scheme_code, date, nav).")


def backfill_nav_from_archive(db_path, scheme_codes=None):
    """One-time backfill from captn3m0/historical-mf-data funds.db (SQLite)."""

    def job(run):
        path = Path(db_path).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(f"Archive not found: {path}")
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            table = _archive_table(con)
            masters = AMFIMasterScheme.objects.all()
            if scheme_codes:
                masters = masters.filter(scheme_code__in=list(scheme_codes))
            by_code = {s.scheme_code: s for s in masters}
            archive_isins = defaultdict(set)
            if "securities" in {r[0] for r in con.execute("SELECT name FROM sqlite_master")}:
                for isin, code in con.execute("SELECT isin, scheme_code FROM securities"):
                    if isin:
                        archive_isins[str(code)].add(str(isin).strip().upper())
            result = JobResult(metadata={"source": "captn3m0/historical-mf-data", "table": table})
            mismatched = []
            current, buffer = None, []

            def flush(code, rows):
                scheme = by_code.get(code)
                if scheme is None or not rows:
                    return
                archive = archive_isins.get(code, set())
                if archive and _scheme_isins(scheme) and not archive & _scheme_isins(scheme):
                    mismatched.append(code)
                    return
                result.parsed += len(rows)
                result.inserted += _insert_missing_navs(scheme, rows, "AMFI_HIST_ARCHIVE")

            for code, raw_date, raw_nav in con.execute(f'SELECT scheme_code, date, nav FROM "{table}" ORDER BY scheme_code'):
                code = str(code)
                if code != current:
                    flush(current, buffer)
                    current, buffer = code, []
                day = _date(raw_date)
                try:
                    value = Decimal(str(raw_nav).strip())
                except Exception:
                    continue
                if day and value.is_finite() and value > 0:
                    buffer.append((day, value))
            flush(current, buffer)
        finally:
            con.close()
        result.metadata["isin_mismatch_schemes"] = sorted(mismatched)
        if mismatched:
            result.unmatched = len(mismatched)
        return result

    return run_job("mf_backfill_nav", "AMFI_HIST_ARCHIVE", job)


def _business_days(start, end) -> int:
    """Mon-Fri days in [start, end] (no exchange-holiday calendar)."""
    if end < start:
        return 0
    days = (end - start).days + 1
    full_weeks, remainder = divmod(days, 7)
    return full_weeks * 5 + sum(1 for i in range(remainder) if (start + timedelta(days=i)).weekday() < 5)


def nav_gap_check(today=None, fetch_json=None, max_requests=None, window_years=3):
    """Weekly: find active schemes whose stored NAV history is stale or sparse."""
    max_requests = max_requests if max_requests is not None else int(os.environ.get("PWMS_MF_GAP_CHECK_MAX_REQUESTS", "300"))

    def job(run):
        latest = AMFIMasterNAV.objects.filter(source="AMFI_NAVALL").aggregate(d=Max("date"))["d"]
        if latest is None:
            return JobResult.skip("NO_NAVALL_DATA_YET")
        start = mf_ratios.window_start(latest, window_years)
        held_ids = set(held_master_schemes().values_list("id", flat=True))
        candidates = AMFIMasterScheme.objects.filter(navall_active=True, is_active=True, is_growth_plan=True)
        stats = {
            row["scheme_id"]: row for row in AMFIMasterNAV.objects.filter(
                scheme__in=candidates, date__gte=start,
            ).values("scheme_id").annotate(n=Count("date", distinct=True), first=Min("date"), last=Max("date"))
        }
        needs = []
        for scheme in candidates.only("id", "scheme_code"):
            row = stats.get(scheme.id)
            if row is None:
                needs.append((scheme.id not in held_ids, scheme.scheme_code, start))
                continue
            business = _business_days(row["first"], latest)
            stale = row["last"] < latest - timedelta(days=3)
            sparse = row["n"] < 0.85 * business
            if stale or sparse:
                needs.append((scheme.id not in held_ids, scheme.scheme_code, start if sparse else row["last"]))
        needs.sort()
        result = JobResult(metadata={"schemes_needing_fill": len(needs), "latest_navall_date": latest.isoformat()})
        fetch = fetch_json or fetch_mfapi_json
        for index, (_, code, from_date) in enumerate(needs):
            if index >= max_requests:
                result.metadata["stopped_at_request_cap"] = max_requests
                break
            _fill_from_mfapi(result, [code], fetch, from_date, latest)
        return result

    return run_job("mf_nav_gap_check", "MFAPI_NAV_HISTORY", job)


# --------------------------------------------------------------------------
# TER (AMFI export via amfipy)
# --------------------------------------------------------------------------
def _financial_year(today) -> str:
    start = today.year if today.month >= 4 else today.year - 1
    return f"{start}-{start + 1}"


def fetch_ter_excel(month):
    client = amfipy_client()
    return with_retries(lambda: client.ter.download_excel(month=month), label="AMFI TER download")


def latest_ter_month(today=None) -> str:
    client = amfipy_client()
    months = with_retries(lambda: client.ter.months(year=_financial_year(today or ist_today())), label="AMFI TER months")
    if not months:
        raise RuntimeError("AMFI returned no TER months.")
    return months[0]["MonthNumber"]


def sync_ter(month=None, fetch_excel=None, fetch_scheme_data=None):
    def job(run):
        target_month = month or latest_ter_month()
        payload = (fetch_excel or fetch_ter_excel)(target_month)
        rows = parse_ter_export(payload, "amfi_ter.xlsx")
        digest = content_hash(rows)
        if digest == last_success_hash("mf_sync_ter"):
            return JobResult.skip("UNCHANGED_FILE_HASH", source_hash=digest, source_url=TER_PAGE_URL)
        _cache_download("amfi_ter", f"ter_{target_month}.xlsx", payload)
        resolver = IdentityResolver.from_scheme_data_text((fetch_scheme_data or fetch_scheme_data_text)())
        result = JobResult(parsed=len(rows), source_hash=digest, source_url=TER_PAGE_URL)
        with DATABASE_SCHEDULER_LOCK:
            for row in rows:
                if row["regular_ter_pct"] is None and row["direct_ter_pct"] is None:
                    result.skipped += 1
                    continue
                schemes = resolver.resolve(MFSchemeIdentityMap.SOURCE_TER_NSDL, row["nsdl_code"], row["parent_name"])
                if not schemes:
                    result.unmatched += 1
                    continue
                for scheme in schemes:
                    created = apply_disclosure(scheme, row["ter_date"], {
                        "ter_regular_pct": row["regular_ter_pct"], "ter_direct_pct": row["direct_ter_pct"],
                        "ter_as_of": row["ter_date"], "ter_source": "AMFI_TER",
                    }, source="AMFI_TER", source_url=TER_PAGE_URL)
                    result.inserted += int(created)
                    result.updated += int(not created)
        result.as_of_date = max((r["ter_date"] for r in rows), default=None)
        result.metadata = {"month": target_month, "identity": dict(resolver.stats), "identity_problems": resolver.problems[:50]}
        return result

    return run_job("mf_sync_ter", "AMFI_TER", job)


# --------------------------------------------------------------------------
# AUM (AMFI scheme-wise average AUM via amfipy, keyed by AMFI code)
# --------------------------------------------------------------------------
def sync_aum(aum_client=None):
    """Load the newest published AMFI AAUM quarter.

    AMFI publishes scheme-wise AUM quarterly (average AUM); it does not publish
    a scheme-wise monthly series, so the quarterly release is always used.
    """

    def job(run):
        client = aum_client or amfipy_client().aum
        years = with_retries(client.financial_years, label="AMFI AAUM years") or []
        for fy in years[:2]:
            periods = parse_aaum_periods(with_retries(lambda: client.periods(fy["id"]), label="AMFI AAUM periods"))
            for period in periods:
                if successful_run_exists("mf_sync_aum", as_of_date=period["quarter_start"]):
                    return JobResult.skip("PERIOD_ALREADY_LOADED", metadata={"quarter": period["label"]})
                data = with_retries(lambda: client.average_aum_schemewise(fy["id"], period["id"]), label="AMFI AAUM data")
                rows = parse_aaum_schemewise(data)
                if rows:
                    return _load_aaum(rows, period)
        return JobResult.skip("NOT_YET_AVAILABLE")

    return run_job("mf_sync_aum", "AMFI_SCHEME_AAUM", job)


def _load_aaum(rows, period):
    schemes = {s.scheme_code: s for s in AMFIMasterScheme.objects.filter(scheme_code__in=[r["amfi_code"] for r in rows])}
    result = JobResult(parsed=len(rows), as_of_date=period["quarter_start"], source_url=AAUM_PAGE_URL,
                       source_hash=content_hash(rows))
    unmatched = 0
    with DATABASE_SCHEDULER_LOCK, transaction.atomic():
        for row in rows:
            scheme = schemes.get(row["amfi_code"])
            if scheme is None:
                unmatched += 1
                continue
            _, created = AMFISchemeAAUM.objects.update_or_create(
                scheme=scheme, quarter_start=period["quarter_start"], source="AMFI_SCHEME_AAUM",
                defaults={
                    "quarter_label": period["label"],
                    "average_aum_cr": (row["amount_lakh"] / Decimal("100")).quantize(Decimal("0.0001")),
                    "source_scheme_name": row["nav_name"][:300],
                },
            )
            result.inserted += int(created)
            result.updated += int(not created)
    result.unmatched = unmatched
    result.metadata = {"quarter": period["label"], "quarter_end": period["quarter_end"].isoformat(), "unmatched_codes": unmatched}
    return result


# --------------------------------------------------------------------------
# AMFI risk parameters (mid/small-cap disclosure) + tracking error
# --------------------------------------------------------------------------
def previous_month_start(today=None) -> date:
    first = (today or ist_today()).replace(day=1)
    return (first - timedelta(days=1)).replace(day=1)


def sync_amfi_risk(report_month=None, risk_client=None, fetch_scheme_data=None, categories=None):
    categories = categories or _env_list("PWMS_MF_RISK_CATEGORY_IDS", ("17", "18"))

    def job(run):
        month = report_month or previous_month_start()
        if successful_run_exists("mf_sync_risk", as_of_date=month):
            return JobResult.skip("PERIOD_ALREADY_LOADED", as_of_date=month)
        client = risk_client or amfipy_client().risk_parameters
        label = month.strftime("%d-%b-%Y")
        rows = []
        for category in categories:
            data = with_retries(lambda: client.fetch(date=label, category_id=category), label="AMFI risk parameters")
            rows.extend(parse_risk_parameters(data, category_id=category))
        rows = [row for row in rows if row["report_month"] == month]
        if not rows:
            return JobResult.skip("NOT_YET_AVAILABLE", as_of_date=month)
        resolver = IdentityResolver.from_scheme_data_text((fetch_scheme_data or fetch_scheme_data_text)())
        result = JobResult(parsed=len(rows), as_of_date=month, source_url=RISK_PAGE_URL,
                           source_hash=content_hash([{k: v for k, v in r.items() if k != "raw"} for r in rows]))
        with DATABASE_SCHEDULER_LOCK:
            for row in rows:
                MFAMFIRiskParameter.objects.update_or_create(
                    risk_scheme_id=row["risk_scheme_id"], report_month=month,
                    defaults={key: row[key] for key in (
                        "category_id", "mf_id", "amc_name", "scheme_name", "aum_cr",
                        "portfolio_std_dev_pct", "benchmark_std_dev_pct", "portfolio_beta",
                        "portfolio_pe", "portfolio_turnover_ratio", "raw",
                    )} | {"source_url": RISK_PAGE_URL, "fetched_at": timezone.now()},
                )
                schemes = resolver.resolve(MFSchemeIdentityMap.SOURCE_RISK_SCHEME_ID, row["risk_scheme_id"], row["scheme_name"])
                if not schemes:
                    result.unmatched += 1
                    continue
                for scheme in schemes:
                    created = apply_disclosure(scheme, month, {
                        "amfi_std_dev_pct": row["portfolio_std_dev_pct"],
                        "amfi_benchmark_std_dev_pct": row["benchmark_std_dev_pct"],
                        "amfi_beta": row["portfolio_beta"],
                        "amfi_risk_as_of": month, "amfi_risk_source": "AMFI_RISK_PARAMETERS",
                    }, source="AMFI_RISK_PARAMETERS", source_url=RISK_PAGE_URL)
                    result.inserted += int(created)
                    result.updated += int(not created)
        result.metadata = {"categories": list(categories), "identity": dict(resolver.stats), "identity_problems": resolver.problems[:50]}
        return result

    return run_job("mf_sync_risk", "AMFI_RISK_PARAMETERS", job)


def previous_month_end(today=None) -> date:
    return (today or ist_today()).replace(day=1) - timedelta(days=1)


def sync_tracking_error(as_of=None, tracking_client=None, fetch_scheme_data=None):
    def job(run):
        target = as_of or previous_month_end()
        client = tracking_client or amfipy_client().tracking
        data = with_retries(lambda: client.error(date=target.strftime("%d-%b-%Y").lower()), label="AMFI tracking error")
        rows = [row for row in parse_tracking_error(data) if row["as_of"] == target]
        if not rows:
            return JobResult.skip("NOT_YET_AVAILABLE", as_of_date=target)
        digest = content_hash(rows)
        if digest == last_success_hash("mf_sync_tracking_error"):
            return JobResult.skip("UNCHANGED_FILE_HASH", source_hash=digest, as_of_date=target)
        resolver = IdentityResolver.from_scheme_data_text((fetch_scheme_data or fetch_scheme_data_text)())
        result = JobResult(parsed=len(rows), as_of_date=target, source_hash=digest, source_url=TRACKING_PAGE_URL)
        with DATABASE_SCHEDULER_LOCK:
            for row in rows:
                schemes = resolver.resolve(MFSchemeIdentityMap.SOURCE_TRACKING_NAME, normalize_key(row["parent_name"]), row["parent_name"])
                if not schemes:
                    result.unmatched += 1
                    continue
                for scheme in schemes:
                    apply_disclosure(scheme, target, {
                        "amfi_tracking_error_regular_pct": row["regular_pct"],
                        "amfi_tracking_error_direct_pct": row["direct_pct"],
                        "amfi_tracking_error_as_of": target,
                        "amfi_tracking_error_source": "AMFI_TRACKING_ERROR",
                    }, source="AMFI_TRACKING_ERROR", source_url=TRACKING_PAGE_URL)
                    result.updated += 1
        result.metadata = {"identity": dict(resolver.stats)}
        return result

    return run_job("mf_sync_tracking_error", "AMFI_TRACKING_ERROR", job)


# --------------------------------------------------------------------------
# Inbox/URL driven sources: risk-free rate, benchmark TRI
# --------------------------------------------------------------------------
def _fetch_bytes(url):
    return PoliteHTTPClient().get(url).content


def _collect_sources(job_name, inbox_dir, urls, fetch_bytes):
    """Yield (label, filename, payload, digest) for new inbox files and configured URLs."""
    found = skipped = 0
    items = []
    for path in _inbox_files(inbox_dir):
        found += 1
        try:
            payload = path.read_bytes()
        except OSError as exc:
            logger.warning("Skipping unreadable inbox file %s: %s", path, exc)
            skipped += 1
            continue
        digest = sha256_bytes(payload)
        if hash_already_processed(job_name, digest):
            skipped += 1
            continue
        items.append((str(path), path.name, payload, digest))
    for url in urls:
        found += 1
        payload = (fetch_bytes or _fetch_bytes)(url)
        digest = sha256_bytes(payload)
        if hash_already_processed(job_name, digest):
            skipped += 1
            continue
        name = Path(url.split("?")[0]).name or "download.csv"
        items.append((url, name if Path(name).suffix else name + ".csv", payload, digest))
    return items, found, skipped


def sync_risk_free(inbox_dir=None, url=None, fetch_bytes=None):
    """91-day T-bill yield from operator-dropped files or a configured URL.

    RBI/FBIL do not offer a documented machine endpoint, so no URL is guessed:
    set PWMS_MF_RISK_FREE_URL or drop CSV/XLSX files (date + yield columns)
    into backend/data/mf_inbox/risk_free/, or use
    ``manage.py mf_sync_risk_free --rate 6.85 --date 2026-11-19``.
    """
    inbox = Path(inbox_dir) if inbox_dir is not None else data_dir("mf_inbox", "risk_free")
    configured = os.environ.get("PWMS_MF_RISK_FREE_URL", "") if url is None else url

    def job(run):
        items, found, skipped = _collect_sources("mf_sync_risk_free", inbox, [configured] if configured else [], fetch_bytes)
        if not found:
            return JobResult.skip("NOT_CONFIGURED")
        if not items:
            return JobResult.skip("UNCHANGED_FILE_HASH", metadata={"files_skipped_unchanged": skipped})
        result = JobResult(metadata={"files_skipped_unchanged": skipped, "files_imported": 0})
        for label, filename, payload, digest in items:
            file_result = JobResult()
            try:
                for row in parse_risk_free_table(payload, filename):
                    _, created = MFRiskFreeRate.objects.update_or_create(
                        tenor=MFRiskFreeRate.TENOR_91D, date=row["date"],
                        defaults={"rate_pct": row["rate_pct"], "source": "OPERATOR_FILE" if not label.startswith("http") else "CONFIGURED_URL",
                                  "source_url": label if label.startswith("http") else ""},
                    )
                    file_result.inserted += int(created)
                    file_result.updated += int(not created)
            except Exception as exc:
                file_result.status = Status.FAILED
                file_result.add_error(file=label, error=exc)
            record_file("mf_sync_risk_free", "RISK_FREE_FILE", digest=digest, path=label, result=file_result)
            result.inserted += file_result.inserted
            result.updated += file_result.updated
            result.failed += file_result.failed
            result.errors.extend(file_result.errors)
            result.metadata["files_imported"] += int(file_result.status != Status.FAILED)
        if result.failed:
            result.status = Status.PARTIAL
        return result

    return run_job("mf_sync_risk_free", "RISK_FREE_91D", job)


def set_risk_free_rate(day, rate_pct, source="MANUAL"):
    obj, _ = MFRiskFreeRate.objects.update_or_create(
        tenor=MFRiskFreeRate.TENOR_91D, date=day,
        defaults={"rate_pct": Decimal(str(rate_pct)).quantize(Decimal("0.0001")), "source": source},
    )
    return obj


def _load_config(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"Invalid pipeline config {path}: {exc}") from exc


def sync_benchmark(inbox_dir=None, config_path=None, fetch_bytes=None):
    """Benchmark TRI history from operator files or configured, licence-cleared URLs.

    Config (backend/data/mf_config/benchmark_sources.json):
        [{"url": "https://...", "source_name": "NSE_INDICES_TRI"}]
    Files: backend/data/mf_inbox/benchmark_tri/*.csv with index name/code,
    date and TRI columns (same layout as sync_benchmark_tri).
    """
    inbox = Path(inbox_dir) if inbox_dir is not None else data_dir("mf_inbox", "benchmark_tri")
    config = _load_config(config_path or data_dir("mf_config", "benchmark_sources.json"), [])

    def job(run):
        names = {entry.get("url"): entry.get("source_name", "CONFIGURED_TRI") for entry in config if entry.get("url")}
        items, found, skipped = _collect_sources("mf_sync_benchmark", inbox, list(names), fetch_bytes)
        if not found:
            return JobResult.skip("NOT_CONFIGURED")
        if not items:
            return JobResult.skip("UNCHANGED_FILE_HASH", metadata={"files_skipped_unchanged": skipped})
        result = JobResult(metadata={"files_skipped_unchanged": skipped})
        for label, filename, payload, digest in items:
            file_result = JobResult()
            try:
                rows = parse_benchmark_tri_rows(payload, filename=filename)
                source = names.get(label, "OPERATOR_FILE")
                objects = [
                    MFBenchmarkTRI(index_code=r["index_code"][:100], index_name=r["index_name"][:300], date=r["date"],
                                   tri_value=r["tri_value"], source=source, source_url=label if label.startswith("http") else "")
                    for r in rows
                ]
                with DATABASE_SCHEDULER_LOCK:
                    MFBenchmarkTRI.objects.bulk_create(
                        objects, batch_size=2000, update_conflicts=True, unique_fields=["index_code", "date"],
                        update_fields=["index_name", "tri_value", "source", "source_url", "fetched_at"],
                    )
                file_result.parsed = file_result.updated = len(objects)
            except Exception as exc:
                file_result.status = Status.FAILED
                file_result.add_error(file=label, error=exc)
            record_file("mf_sync_benchmark", "BENCHMARK_TRI_FILE", digest=digest, path=label, result=file_result)
            result.parsed += file_result.parsed
            result.updated += file_result.updated
            result.failed += file_result.failed
            result.errors.extend(file_result.errors)
        if result.failed:
            result.status = Status.PARTIAL
        return result

    return run_job("mf_sync_benchmark", "BENCHMARK_TRI", job)


# --------------------------------------------------------------------------
# Holdings (AMC monthly / fortnightly portfolio disclosures)
# --------------------------------------------------------------------------
FILENAME_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})(?:__(\d+))?")


def _holdings_targets(today):
    """Portfolio dates to look for: previous month-end, plus the 15th for debt schemes."""
    targets = [previous_month_end(today)]
    if today.day >= 16:
        targets.append(today.replace(day=15))
    return targets


def _render_url(template, day):
    return template.format(
        yyyy=day.strftime("%Y"), yy=day.strftime("%y"), mm=day.strftime("%m"), dd=day.strftime("%d"),
        mon=day.strftime("%b"), month=day.strftime("%B"), MON=day.strftime("%b").upper(),
        MONTH=day.strftime("%B").upper(), month_lower=day.strftime("%B").lower(),
    )


def _top10(scheme, portfolio_date):
    rows = MFMasterHolding.objects.filter(scheme=scheme, portfolio_date=portfolio_date).exclude(pct_of_nav__isnull=True).order_by("-pct_of_nav")[:10]
    return [
        {
            "security_name": row.security_name, "security_isin": row.isin, "sector": row.sector,
            "asset_type": row.instrument_type, "pct_of_nav": str(row.pct_of_nav),
            "market_value": str(row.market_value) if row.market_value is not None else None,
        }
        for row in rows
    ]


def _import_holdings_file(parser, label, filename, payload, portfolio_date, pinned_code):
    result = JobResult(as_of_date=portfolio_date)
    rows = parser.parse(payload, filename, portfolio_date)
    result.parsed = len(rows)
    pinned = AMFIMasterScheme.objects.filter(scheme_code=pinned_code).first() if pinned_code else None
    if pinned_code and pinned is None:
        raise ValueError(f"Unknown AMFI scheme code {pinned_code} in filename {filename}.")
    grouped = defaultdict(list)
    for row in rows:
        if pinned:
            scheme = pinned
        elif row.scheme_code:
            scheme = AMFIMasterScheme.objects.filter(scheme_code=row.scheme_code).first()
        else:
            scheme, _ = resolve_master_scheme(isin=row.scheme_isin, scheme_name=row.scheme_name)
        if scheme is None:
            result.unmatched += 1
            continue
        if not parser.matches(scheme.amc_name):
            result.skipped += 1
            continue
        grouped[scheme].append(row.to_persist_dict())
    source = f"AMC_PORTFOLIO_{parser.code.upper()}"
    with DATABASE_SCHEDULER_LOCK:
        for scheme, persist_rows in grouped.items():
            counts = persist_portfolio_rows(scheme, persist_rows, portfolio_date=portfolio_date, source=source,
                                            source_url=label if label.startswith("http") else "")
            result.inserted += counts["inserted"]
            result.updated += counts["updated"]
            result.skipped += counts["skipped"]
            apply_disclosure(scheme, portfolio_date, {
                "top10_holdings": _top10(scheme, portfolio_date),
                "holdings_as_of": portfolio_date, "holdings_source": source,
            }, source=source, source_url=label if label.startswith("http") else "")
    result.metadata = {"schemes": len(grouped), "file": filename}
    return result


def sync_holdings(inbox_dir=None, config_path=None, today=None, fetch_bytes=None, amc=None):
    """Import AMC portfolio disclosures for the AMCs PWMS users actually hold.

    Sources per registered AMC parser (no URLs are guessed):
      * files in backend/data/mf_inbox/holdings/<amc_code>/ named
        ``YYYY-MM-DD[__<amfi_scheme_code>][__anything].<xlsx|csv|tsv>``
        (date = portfolio date; scheme code only for single-scheme files);
      * URL templates in backend/data/mf_config/holdings_sources.json, e.g.
        {"hdfc": {"url_template": "https://.../{month}-{yyyy}.xlsx"}}.
    Held AMCs without a parser are logged as unsupported.
    """
    inbox = Path(inbox_dir) if inbox_dir is not None else data_dir("mf_inbox", "holdings")
    config = _load_config(config_path or data_dir("mf_config", "holdings_sources.json"), {})

    def job(run):
        held = held_master_schemes()
        held_amcs = sorted({name for name in held.values_list("amc_name", flat=True) if name})
        if not held_amcs:
            return JobResult.skip("NO_HELD_SCHEMES")
        day = today or ist_today()
        parsers, unsupported = {}, []
        for amc_name in held_amcs:
            parser = holdings_registry.parser_for_amc(amc_name)
            if parser is None:
                unsupported.append(amc_name)
            elif amc is None or parser.code == amc:
                parsers[parser.code] = parser
        result = JobResult(metadata={"held_amcs": held_amcs, "unsupported_amcs": unsupported,
                                     "files_imported": 0, "files_skipped_unchanged": 0, "amcs_without_source": []})
        for code, parser in parsers.items():
            sources = []
            for path in _inbox_files(inbox / code):
                match = FILENAME_RE.match(path.name)
                if not match:
                    result.add_error(file=path.name, error="Filename must start with YYYY-MM-DD (portfolio date).")
                    continue
                sources.append((str(path), path.name, date.fromisoformat(match.group(1)), match.group(2) or "", path.read_bytes))
            template = (config.get(code) or {}).get("url_template")
            if template:
                for target in _holdings_targets(day):
                    url = _render_url(template, target)
                    sources.append((url, Path(url.split("?")[0]).name or f"{code}.xlsx", target, "",
                                    lambda url=url: (fetch_bytes or _fetch_bytes)(url)))
            if not sources:
                result.metadata["amcs_without_source"].append(parser.canonical_name)
                continue
            for label, filename, portfolio_date, pinned_code, read in sources:
                try:
                    payload = read()
                except Exception as exc:
                    result.add_error(file=label, error=exc)  # not yet published, or unreachable
                    continue
                digest = sha256_bytes(payload)
                if hash_already_processed("mf_sync_holdings", digest):
                    result.metadata["files_skipped_unchanged"] += 1
                    continue
                try:
                    file_result = _import_holdings_file(parser, label, filename, payload, portfolio_date, pinned_code)
                except Exception as exc:
                    file_result = JobResult(status=Status.FAILED)
                    file_result.add_error(file=label, error=exc)
                record_file("mf_sync_holdings", f"AMC_PORTFOLIO_{code.upper()}", digest=digest, path=label,
                            result=file_result, as_of_date=portfolio_date)
                for attr in ("parsed", "inserted", "updated", "skipped", "unmatched", "failed"):
                    setattr(result, attr, getattr(result, attr) + getattr(file_result, attr))
                result.errors.extend(file_result.errors)
                result.metadata["files_imported"] += int(file_result.status != Status.FAILED)
        if unsupported or result.failed or result.metadata["amcs_without_source"]:
            result.status = Status.PARTIAL
        if not result.metadata["files_imported"] and not result.failed and not result.metadata["files_skipped_unchanged"]:
            if not unsupported:
                return JobResult.skip("NO_SOURCE_FILES", metadata=result.metadata)
        return result

    return run_job("mf_sync_holdings", "AMC_PORTFOLIO", job)


# --------------------------------------------------------------------------
# Daily calculation: build snapshot + overlay calculated ratios & NAV
# --------------------------------------------------------------------------
def _calc_scope_codes(scope):
    scope = scope or os.environ.get("PWMS_MF_CALC_SCOPE", "growth")
    if scope == "held":
        return sorted(held_master_schemes().values_list("scheme_code", flat=True))
    qs = AMFIMasterScheme.objects.filter(is_active=True, navall_active=True)
    if scope != "all":
        qs = qs.filter(is_growth_plan=True)
    return sorted(qs.values_list("scheme_code", flat=True))


def _nav_series(scheme, as_of):
    best = {}
    for day, nav, source in AMFIMasterNAV.objects.filter(scheme=scheme, date__lte=as_of).values_list("date", "nav", "source"):
        if nav is None or nav <= 0:
            continue
        current = best.get(day)
        if current is None or NAV_SOURCE_PRIORITY.get(source, 0) > NAV_SOURCE_PRIORITY.get(current[1], 0):
            best[day] = (nav, source)
    return sorted(best.items())


class _BenchmarkIndex:
    def __init__(self):
        codes = defaultdict(set)
        for code, name in MFBenchmarkTRI.objects.values_list("index_code", "index_name").distinct():
            codes[normalize_key(name)].add(code)
        self.codes = codes
        self.series = {}

    def series_for(self, benchmark_name, as_of):
        matches = self.codes.get(normalize_key(benchmark_name)) if benchmark_name else None
        if not matches or len(matches) != 1:
            return "", None
        code = next(iter(matches))
        if code not in self.series:
            self.series[code] = list(MFBenchmarkTRI.objects.filter(index_code=code).order_by("date").values_list("date", "tri_value"))
        return code, [(d, v) for d, v in self.series[code] if d <= as_of]


def _risk_free_for(as_of):
    row = MFRiskFreeRate.objects.filter(tenor=MFRiskFreeRate.TENOR_91D, date__lte=as_of).order_by("-date").first()
    return (row.rate_pct, False) if row else (DEFAULT_RISK_FREE_PCT, True)


def _overlay(scheme, as_of, rf_pct, rf_default, benchmarks):
    snapshot = MFMasterData.objects.filter(scheme=scheme, as_of_date=as_of).first()
    if snapshot is None:
        return False
    series = _nav_series(scheme, as_of)
    values = {}
    flags = set(snapshot.data_quality_flags or [])
    lineage = dict(snapshot.field_sources or {})
    stamp = timezone.now().isoformat()
    if series:
        last_day, (last_nav, last_source) = series[-1]
        values.update({"nav": Decimal(last_nav).quantize(Decimal("0.0001")), "nav_as_of": last_day, "nav_source": last_source})
    fund = [(d, float(v[0])) for d, v in series] if scheme.is_growth_plan else []
    bench_code, bench_series = benchmarks.series_for(snapshot.benchmark_name, as_of)
    ratios = mf_ratios.compute_ratio_set(fund, [(d, float(v)) for d, v in bench_series] if bench_series else None,
                                         as_of=as_of, risk_free_rate_pct=rf_pct)
    if not scheme.is_growth_plan:
        flags.add("CALC_RATIOS_SKIPPED_NON_GROWTH_OPTION")
    flags.update(ratios.pop("flags"))
    if rf_default:
        flags.add("RISK_FREE_RATE_DEFAULT_USED")
    values.update(ratios)
    values.update({"calc_benchmark_index_code": bench_code, "calc_ratios_as_of": as_of, "calc_ratios_source": "PWMS_MF_RATIOS"})
    for name, value in values.items():
        setattr(snapshot, name, value)
        if name.startswith("calc_") or name.startswith("nav"):
            if value is None or value == "":
                lineage.pop(name, None)
            else:
                lineage[name] = {"source": "PWMS_MF_RATIOS" if name.startswith("calc_") else values.get("nav_source", ""),
                                 "source_url": "", "as_of_date": as_of.isoformat(), "fetched_at": stamp}
    snapshot.field_sources = lineage
    snapshot.data_quality_flags = sorted(flags)
    snapshot.save()
    return True


def calc_ratios(as_of=None, scheme_codes=None, scope=None, chunk_size=100):
    """Rebuild today's snapshot (build_mf_masterdata) and add calc_*/NAV fields."""

    def job(run):
        target = as_of or AMFIMasterNAV.objects.filter(source="AMFI_NAVALL").aggregate(d=Max("date"))["d"]
        if target is None:
            return JobResult.skip("NO_NAVALL_DATA_YET")
        codes = sorted(scheme_codes) if scheme_codes else _calc_scope_codes(scope)
        scope_key = content_hash({"as_of": target, "codes": codes})
        if successful_run_exists("mf_calc_ratios", as_of_date=target, source_file_hash=scope_key):
            return JobResult.skip("ALREADY_CALCULATED_FOR_NAV_DATE", as_of_date=target)
        rf_pct, rf_default = _risk_free_for(target)
        benchmarks = _BenchmarkIndex()
        result = JobResult(as_of_date=target, source_hash=scope_key,
                           metadata={"schemes": len(codes), "risk_free_rate_pct": str(rf_pct), "risk_free_default_used": rf_default})
        for start in range(0, len(codes), chunk_size):
            chunk = codes[start:start + chunk_size]
            with DATABASE_SCHEDULER_LOCK:
                try:
                    call_command("build_mf_masterdata", as_of=target.isoformat(), scheme_code=chunk, stdout=io.StringIO())
                except Exception as exc:
                    result.add_error(chunk_start=chunk[0], error=exc)
                    continue
                for scheme in AMFIMasterScheme.objects.filter(scheme_code__in=chunk):
                    try:
                        with transaction.atomic():
                            result.updated += int(_overlay(scheme, target, rf_pct, rf_default, benchmarks))
                    except Exception as exc:
                        result.add_error(scheme_code=scheme.scheme_code, error=exc)
        result.parsed = len(codes)
        if result.failed:
            result.status = Status.PARTIAL
        return result

    return run_job("mf_calc_ratios", "PWMS_MF_RATIOS", job)


# --------------------------------------------------------------------------
# Backfill entry point used by the scheduler / mf_backfill_nav
# --------------------------------------------------------------------------
def backfill_nav(source="mfapi", db_path=None, scope="held", max_requests=None):
    if source == "archive":
        if not db_path:
            raise ValueError("--db-path to an extracted funds.db is required for --source archive.")
        return backfill_nav_from_archive(db_path)
    codes = _calc_scope_codes(scope)
    return fill_nav_from_mfapi(scheme_codes=codes, max_requests=max_requests)
