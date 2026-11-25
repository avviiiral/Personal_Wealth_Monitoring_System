"""Scheduled jobs owned by mutual_funds.

* mf_nav         nightly 00:00 (and at startup when not current): AMFI NAVs.
* sip            right after mf_nav succeeds: SIP installment sync + execution.
* mf_underlying  nightly 03:00: mutual-fund underlying holdings.
"""
import logging
from datetime import datetime
from io import StringIO

from django.core.management import call_command

from config.scheduling.clock import last_weekday_before, last_weekday_on_or_before, localize, parse_hhmm
from config.scheduling.framework import DailySchedule, ScheduledJob, max_age_is_current, slot_is_current
from config.scheduling.registry import job_config

logger = logging.getLogger("mutual_funds.jobs")


def expected_publish_date(now, cutoff_hhmm):
    """Latest NAV date AMFI should have published by ``now``.

    After the cutoff (default 23:00) that is today's date, or the last
    weekday if today is a weekend; before the cutoff it is the last weekday
    before today.
    """
    now = localize(now)
    if now.time() >= parse_hhmm(cutoff_hhmm):
        return last_weekday_on_or_before(now.date())
    return last_weekday_before(now.date())


def _fund_assets():
    from investments.models import Asset
    from market_data.services.market_price_scope import mutual_fund_q

    return Asset.objects.filter(is_active=True).filter(mutual_fund_q()).order_by("name", "id")


class MutualFundNavJob(ScheduledJob):
    def __init__(self):
        config = job_config("mf_nav")
        self.hhmm = config.get("time", "00:00")
        self.cutoff = config.get("publish_cutoff", "23:00")
        parse_hhmm(self.cutoff)
        super().__init__(name="mf_nav", schedule=DailySchedule(self.hhmm), enabled=config.get("enabled", True))

    def is_current(self, now, *, last_success, at_startup):
        from django.db.models import Max, Q

        from market_data.models import DataSource

        expected = expected_publish_date(now, self.cutoff)
        funds = list(
            _fund_assets()
            .exclude(isin__isnull=True)
            .exclude(isin="")
            .annotate(latest_nav=Max("market_prices__date", filter=Q(market_prices__source=DataSource.AMFI)))
        )
        if not funds:
            return True, "no mutual funds with an ISIN"
        behind = [fund for fund in funds if fund.latest_nav is None or fund.latest_nav < expected]
        if not behind:
            return True, f"all {len(funds)} fund(s) have a NAV dated {expected} or later"
        cutoff_moment = datetime.combine(expected, parse_hhmm(self.cutoff), tzinfo=localize(now).tzinfo)
        if last_success is not None and localize(last_success) >= cutoff_moment:
            return True, (
                f"{len(behind)} fund(s) have no NAV for {expected}, but the job already ran after the "
                f"{cutoff_moment:%d %b %H:%M} cutoff (holiday, or not published)"
            )
        return False, f"{len(behind)} of {len(funds)} fund(s) have no NAV dated {expected}"

    def run(self, now):
        from django.contrib.auth.models import User

        from market_data.services.market_data_manager import MarketDataManager
        from market_data.services.mutual_fund_nav_service import MutualFundNAVService
        from mutual_funds.services.amfi import AMFIService
        from mutual_funds.services.investment_amfi import InvestmentAMFIService

        # NAVAll.txt is cached per calendar day; drop it so a NAV published
        # after an earlier download today is seen.
        MutualFundNAVService.clear_cache()
        step_errors = []

        # 1. AMFI master refresh for owned ISINs + MIS history prefetch.
        try:
            amfi = InvestmentAMFIService.refresh_for_investments_with_history() or {}
        except Exception as exc:  # noqa: BLE001 - steps 2-3 still update portfolio NAVs
            amfi = {}
            step_errors.append(f"AMFI master refresh: {type(exc).__name__}: {exc}")
            logger.debug("AMFI master refresh failed", exc_info=True)

        # 2. Latest NAV into MarketPrice + holding rebuild, per fund with an ISIN.
        counts = {"funds": 0, "updated": 0, "skipped": 0, "failed": 0, "no_isin": 0, "users": 0, "user_sync_failed": 0}
        failures = []
        for asset in _fund_assets():
            if not (asset.isin or "").strip():
                counts["no_isin"] += 1
                logger.debug("NAV skipped for %s: no ISIN", asset.name)
                continue
            counts["funds"] += 1
            try:
                result = MarketDataManager._fetch_amfi_nav(asset) or {}
            except Exception as exc:  # noqa: BLE001
                counts["failed"] += 1
                failures.append(f"{asset.name}: {exc}")
                continue
            if result.get("success") and not result.get("skipped"):
                counts["updated"] += 1
            elif result.get("skipped"):
                counts["skipped"] += 1
                logger.debug("NAV skipped for %s: %s", asset.name, result.get("reason"))
            else:
                counts["failed"] += 1
                failures.append(f"{asset.name}: {result.get('error') or result.get('reason')}")

        # 3. Materialize master NAVs into each user's family scheme tables.
        for user in User.objects.filter(is_active=True).order_by("id"):
            counts["users"] += 1
            try:
                AMFIService.sync_owned_navs_from_master(user)
            except Exception as exc:  # noqa: BLE001
                counts["user_sync_failed"] += 1
                failures.append(f"user {user.id} family sync: {exc}")

        counts["amfi_requested"] = amfi.get("requested_isins", 0)
        counts["amfi_matched"] = amfi.get("matched_isins", 0)
        counts["amfi_unmatched"] = len(amfi.get("unmatched_isins") or [])
        if counts["no_isin"]:
            logger.info("mf_nav: %s fund(s) have no ISIN and were skipped (AMFI matches by ISIN).", counts["no_isin"])
        if failures:
            shown = "; ".join(failures[:5]) + (f" (+{len(failures) - 5} more)" if len(failures) > 5 else "")
            logger.warning("mf_nav: %s NAV update(s) failed: %s", len(failures), shown)
        if step_errors:
            # Fail the job so it is retried and the SIP job waits; the
            # portfolio NAVs from step 2 are already stored.
            raise RuntimeError("; ".join(step_errors))
        return counts


class SipJob(ScheduledJob):
    """SIP installment sync + execution, right after a successful NAV job.

    Uses the existing commands unchanged; SIP execution creates the SIP's
    mutual-fund transactions exactly as the old daily refresh did.
    """

    def __init__(self):
        config = job_config("sip")
        self.parent_hhmm = job_config("mf_nav").get("time", "00:00")
        super().__init__(
            name="sip", schedule=None, after="mf_nav", requires_parent_success=True,
            enabled=config.get("enabled", True),
        )

    def is_current(self, now, *, last_success, at_startup):
        return slot_is_current(now, self.parent_hhmm, last_success)

    def run(self, now):
        from django.contrib.auth.models import User

        counts = {"users": 0, "failed_users": 0}
        errors = []
        for user_id in User.objects.filter(is_active=True).order_by("id").values_list("id", flat=True):
            counts["users"] += 1
            try:
                call_command("sync_sip_installments", user_id=user_id)
                call_command("execute_sips", user_id=user_id)
            except Exception as exc:  # noqa: BLE001 - other users still run
                counts["failed_users"] += 1
                errors.append(f"user {user_id}: {exc}")
        if errors:
            raise RuntimeError("; ".join(errors[:5]))
        return counts


class MutualFundUnderlyingJob(ScheduledJob):
    def __init__(self):
        config = job_config("mf_underlying")
        self.hhmm = config.get("time", "03:00")
        self.max_age_hours = float(config.get("max_age_hours", 24))
        self.min_gap_hours = float(config.get("min_gap_hours", 12))
        super().__init__(name="mf_underlying", schedule=DailySchedule(self.hhmm), enabled=config.get("enabled", True))

    def is_current(self, now, *, last_success, at_startup):
        return max_age_is_current(
            now, self.hhmm, last_success, at_startup=at_startup,
            max_age_hours=self.max_age_hours, min_gap_hours=self.min_gap_hours,
        )

    def run(self, now):
        out, err = StringIO(), StringIO()
        call_command("fetch_mf_underlying", stdout=out, stderr=err)
        summary = (err.getvalue() or out.getvalue()).strip().splitlines()
        if err.getvalue().strip():
            logger.warning("mf_underlying: some schemes failed: %s", summary[-1][:500] if summary else "")
        return {"result": (summary[-1][:200] if summary else "")}
