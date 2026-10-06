"""Automatic AMFI NAV history for the MIS report.

The MIS page must not download anything while a user waits. Instead, the
history it needs is stored ahead of time, automatically:

* right after a transaction upload (``investments.services.auto_price_refresh``)
* during the daily scheduled refresh (``run_scheduled_refresh``)

Only the schemes actually held by a family are downloaded, and only the part
of the history that is not already stored, so repeated runs are cheap and
idempotent. Everything is written to the shared ``AMFIMasterNAV`` table, which
is where ``MISReportService`` already reads historical NAVs from.
"""
import logging
import threading
from datetime import date, timedelta

from django.db.models import Max, Min

from mutual_funds.models import AMFIMasterNAV, AMFIMasterScheme
from mutual_funds.services.amfi import AMFIService

from .mis_report_service import MISReportService

logger = logging.getLogger(__name__)

# How far back to keep history for a held scheme (about five years).
HISTORY_LOOKBACK_DAYS = 1830
# NAV on/before the first transaction date is never needed beyond this buffer.
HEAD_BUFFER_DAYS = 7
# Re-download a few days before the newest stored NAV so late corrections
# and any small gap at the tail are repaired.
TAIL_OVERLAP_DAYS = 3
# Newest stored NAV within this many days of today counts as up to date
# (covers weekends/holidays).
TAIL_FRESH_DAYS = 4
# A scheme whose newest NAV is older than this is treated as closed/merged and
# is not polled again, so matured funds never trigger repeat downloads.
INACTIVE_AFTER_DAYS = 45

_running_family_ids = set()
_running_lock = threading.Lock()


class MISHistoryPrefetch:
    @classmethod
    def run_for_assets(cls, asset_ids):
        """Prefetch for every family that owns the given assets."""
        from investments.models import Asset
        from users.models import FamilyGroup

        family_ids = set(
            Asset.objects
            .filter(id__in=list(asset_ids or []), family__isnull=False)
            .values_list("family_id", flat=True)
        )
        summaries = []
        for family in FamilyGroup.objects.filter(id__in=family_ids):
            summaries.append(cls.run_for_family(family))
        return summaries

    @classmethod
    def run_for_all_families(cls):
        from users.models import FamilyGroup

        totals = {"families": 0, "schemes": 0, "requests": 0, "failed": 0}
        for family in FamilyGroup.objects.all():
            result = cls.run_for_family(family)
            if result.get("skipped"):
                continue
            totals["families"] += 1
            totals["schemes"] += result["schemes"]
            totals["requests"] += result["requests"]
            totals["failed"] += result["failed"]
        return totals

    @classmethod
    def run_for_family(cls, family, today=None):
        family_id = family.pk
        with _running_lock:
            if family_id in _running_family_ids:
                return {"skipped": True}
            _running_family_ids.add(family_id)

        try:
            return cls._prefetch(family, today or date.today())
        finally:
            with _running_lock:
                _running_family_ids.discard(family_id)

    @classmethod
    def _plan(cls, earliest_by_code, today):
        """Return ``{start_date: [scheme_code, ...]}`` of history still needed."""
        codes = sorted(earliest_by_code)
        master_ids = dict(
            AMFIMasterScheme.objects
            .filter(scheme_code__in=codes, is_active=True)
            .values_list("scheme_code", "id")
        )
        coverage = {
            item["scheme_id"]: (item["first"], item["last"])
            for item in (
                AMFIMasterNAV.objects
                .filter(scheme_id__in=master_ids.values())
                .values("scheme_id")
                .annotate(first=Min("date"), last=Max("date"))
            )
        }

        floor = today - timedelta(days=HISTORY_LOOKBACK_DAYS)
        plan = {}
        for code in codes:
            master_id = master_ids.get(code)
            if master_id is None:
                logger.debug("MIS prefetch: no AMFI master row for %s.", code)
                continue

            earliest = earliest_by_code[code] or today
            need_from = max(earliest - timedelta(days=HEAD_BUFFER_DAYS), floor)
            first, last = coverage.get(master_id, (None, None))

            if first is None:
                start = need_from
            elif first > need_from + timedelta(days=HEAD_BUFFER_DAYS):
                start = need_from
            else:
                if last >= today - timedelta(days=TAIL_FRESH_DAYS):
                    continue
                if last < today - timedelta(days=INACTIVE_AFTER_DAYS):
                    continue
                start = max(last - timedelta(days=TAIL_OVERLAP_DAYS), need_from)

            if start <= today:
                plan.setdefault(start, []).append(code)
        return plan

    @classmethod
    def _import(cls, start, today, codes):
        """Import one batch. Returns ``(requests, failed_codes)``.

        AMFI resolution is all-or-nothing per batch, so if a batch fails the
        codes are retried one by one; a single unresolvable scheme (for
        example a closed-ended fund) cannot block the rest.
        """
        try:
            AMFIService.import_historical_master_navs(
                start, today, scheme_codes=codes
            )
            return 1, []
        except Exception:
            if len(codes) == 1:
                logger.exception(
                    "MIS prefetch: AMFI history import failed for %s.", codes[0]
                )
                return 1, list(codes)
            logger.warning(
                "MIS prefetch: batch import failed for %s schemes; retrying individually.",
                len(codes),
            )

        requests_made = 1
        failed = []
        for code in codes:
            try:
                AMFIService.import_historical_master_navs(
                    start, today, scheme_codes=[code]
                )
            except Exception:
                logger.exception(
                    "MIS prefetch: AMFI history import failed for %s.", code
                )
                failed.append(code)
            requests_made += 1
        return requests_made, failed

    @classmethod
    def _prefetch(cls, family, today):
        summary = {"schemes": 0, "requests": 0, "failed": 0}

        rows = MISReportService._base_rows(family)
        earliest_by_code = MISReportService._collect_mf_scheme_codes(rows)
        if not earliest_by_code:
            return summary

        plan = cls._plan(earliest_by_code, today)
        for start in sorted(plan):
            codes = plan[start]
            logger.info(
                "MIS prefetch: importing AMFI history %s to %s for %s scheme(s).",
                start.isoformat(),
                today.isoformat(),
                len(codes),
            )
            requests_made, failed = cls._import(start, today, codes)
            summary["schemes"] += len(codes)
            summary["requests"] += requests_made
            summary["failed"] += len(failed)
        return summary
