"""Ingestion log for pipeline jobs, stored in mf_source_runs (MFSourceRun).

Spec field -> MFSourceRun column:
    job_name -> command        started_at/finished_at -> started_at/completed_at
    status   -> status         rows_inserted/updated/failed -> rows_*
    error_message -> errors    source_file_hash -> source_file_hash
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Callable

from django.utils import timezone

from mutual_funds.master_models import MFSourceRun

logger = logging.getLogger(__name__)
Status = MFSourceRun.Status
DONE_STATUSES = (Status.SUCCESS, Status.PARTIAL)


@dataclass
class JobResult:
    status: str = Status.SUCCESS
    parsed: int = 0
    inserted: int = 0
    updated: int = 0
    skipped: int = 0
    unmatched: int = 0
    failed: int = 0
    errors: list = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
    source_hash: str = ""
    source_url: str = ""
    as_of_date: date | None = None

    @classmethod
    def skip(cls, reason: str, **kwargs) -> "JobResult":
        metadata = {"skip_reason": reason, **kwargs.pop("metadata", {})}
        return cls(status=Status.SKIPPED, metadata=metadata, **kwargs)

    def add_error(self, **error) -> None:
        self.failed += 1
        if len(self.errors) < 100:
            self.errors.append({key: str(value)[:500] for key, value in error.items()})


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def content_hash(rows) -> str:
    """Hash of parsed content (used where the raw file embeds timestamps, e.g. XLSX)."""
    return sha256_bytes(json.dumps(rows, sort_keys=True, default=str).encode("utf-8"))


def last_success_hash(job_name: str) -> str:
    run = (
        MFSourceRun.objects.filter(command=job_name, status__in=DONE_STATUSES)
        .exclude(source_file_hash="").order_by("-started_at", "-id").first()
    )
    return run.source_file_hash if run else ""


def hash_already_processed(job_name: str, digest: str) -> bool:
    return MFSourceRun.objects.filter(
        command=job_name, source_file_hash=digest, status__in=DONE_STATUSES,
    ).exists()


def last_success_at(job_name: str):
    run = (
        MFSourceRun.objects.filter(command=job_name, status__in=DONE_STATUSES + (Status.SKIPPED,))
        .order_by("-completed_at").values_list("completed_at", flat=True).first()
    )
    return run


def successful_run_exists(job_name: str, **filters) -> bool:
    return MFSourceRun.objects.filter(command=job_name, status__in=DONE_STATUSES, **filters).exists()


def _finish(run: MFSourceRun, result: JobResult) -> MFSourceRun:
    run.completed_at = timezone.now()
    run.status = result.status
    run.rows_parsed = result.parsed
    run.rows_inserted = result.inserted
    run.rows_updated = result.updated
    run.rows_skipped = result.skipped
    run.rows_unmatched = result.unmatched
    run.rows_failed = result.failed
    run.errors = result.errors[:100]
    run.metadata = result.metadata
    run.source_file_hash = result.source_hash or ""
    if result.source_url:
        run.source_url = result.source_url[:1000]
    if result.as_of_date:
        run.as_of_date = result.as_of_date
    run.save()
    return run


def run_job(job_name: str, source: str, func: Callable[[MFSourceRun], JobResult], *, as_of_date=None) -> MFSourceRun:
    """Execute ``func`` and record the outcome. Never raises; failures are logged.

    Connection hygiene (close_old_connections) is done by the scheduler around
    each job, not here: closing inside a transaction would break callers such
    as Django TestCase on PostgreSQL.
    """
    run = MFSourceRun.objects.create(
        source=source, command=job_name, as_of_date=as_of_date, status=Status.RUNNING,
    )
    try:
        result = func(run)
    except Exception as exc:
        logger.exception("Masterdata job %s failed.", job_name)
        result = JobResult(status=Status.FAILED, failed=1, errors=[{"error": str(exc)[:1000]}])
    return _finish(run, result)


def record_file(job_name: str, source: str, *, digest: str, path: str, result: JobResult, as_of_date=None) -> MFSourceRun:
    """One child log row per imported file, so unchanged files are skipped later."""
    run = MFSourceRun.objects.create(
        source=source, command=job_name, as_of_date=as_of_date,
        raw_file_path=path[:1000], status=Status.RUNNING,
    )
    result.source_hash = digest
    return _finish(run, result)
