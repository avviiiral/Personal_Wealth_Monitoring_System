"""Scheduled jobs owned by market_data.

* market_prices    every 15 minutes: stocks, ETFs, SGBs, listed bonds and the
                   MIS reference tickers. Mutual funds are not in this loop.
* security_master  nightly 02:00: refresh backend/data/security_master.xlsx.
* security_metrics nightly 02:30: P/E, P/B, ROE etc. from Yahoo Finance.
"""
import csv
import io
import logging
from datetime import timedelta

from config.scheduling.clock import MarketCalendar, localize
from config.scheduling.deadline import DeadlineExceeded, DeadlineExecutor
from config.scheduling.framework import (
    DailySchedule,
    IntervalSchedule,
    JobSkipped,
    ScheduledJob,
    max_age_is_current,
)
from config.scheduling.registry import job_config
from market_data.services.price_freshness import PriceFreshness

logger = logging.getLogger("market_data.jobs")

MAX_LISTED_FAILURES = 5


def _summarise(failures):
    shown = "; ".join(f"{name}: {reason}" for name, reason in failures[:MAX_LISTED_FAILURES])
    extra = len(failures) - MAX_LISTED_FAILURES
    return shown + (f" (+{extra} more)" if extra > 0 else "")


class FailureBackoff:
    """Retry a key that keeps failing on a slower schedule.

    A failure only counts while the provider is working for something else:
    some other key succeeded in the same pass, or since this key's streak
    began. So a stale or delisted symbol is parked after ``threshold``
    failed passes, but a provider-wide outage never parks anything.
    A parked key is retried once per ``retry``; a success clears it.
    In memory only: a restart retries everything once.
    """

    def __init__(self, threshold, retry):
        self.threshold = int(threshold)
        self.retry = retry
        self._streaks = {}
        self._until = {}
        self._last_provider_success = None

    @property
    def enabled(self):
        return self.threshold > 0 and self.retry.total_seconds() > 0

    @property
    def tracking(self):
        """True while any key has a failure streak or is parked."""
        return bool(self._streaks or self._until)

    def deferred(self, key, now):
        until = self._until.get(key)
        return self.enabled and until is not None and now < until

    def deferred_keys(self, now):
        return [key for key in self._until if self.deferred(key, now)]

    def record_pass(self, now, *, succeeded, failed, provider_ok):
        """Update streaks after a pass. ``failed`` maps key -> info dict.
        Returns (newly_parked, changed) where newly_parked lists (key, entry)."""
        if provider_ok:
            self._last_provider_success = now
        changed = False
        for key in succeeded:
            self._streaks.pop(key, None)
            if self._until.pop(key, None) is not None:
                changed = True
        newly = []
        for key, info in failed.items():
            streak = self._streaks.get(key)
            since = streak["since"] if streak else now
            if self._last_provider_success is None or self._last_provider_success < since:
                continue
            entry = {"count": (streak["count"] if streak else 0) + 1, "since": since, "info": info}
            self._streaks[key] = entry
            if self.enabled and entry["count"] >= self.threshold:
                if key not in self._until:
                    newly.append((key, entry))
                    changed = True
                self._until[key] = now + self.retry
        return newly, changed

    def parked(self):
        return [
            {"key": key, "until": until, **self._streaks.get(key, {"count": 0, "info": {}})}
            for key, until in sorted(self._until.items(), key=lambda item: str(item[0]))
        ]


class MarketPricesJob(ScheduledJob):
    def __init__(self, *, calendar=None, deadline_seconds=None, mis_reference=None):
        config = job_config("market_prices")
        super().__init__(
            name="market_prices",
            schedule=IntervalSchedule(config.get("interval_minutes", 15)),
            enabled=config.get("enabled", True),
            log_current_at_info=False,
        )
        self.calendar = calendar or MarketCalendar.from_config()
        self.freshness = PriceFreshness(self.calendar)
        self.deadline_seconds = float(
            config.get("per_asset_deadline_seconds", 45) if deadline_seconds is None else deadline_seconds
        )
        self.max_hung_calls = int(config.get("max_hung_provider_calls", 3))
        self.unresolved_retry = timedelta(hours=float(config.get("unresolved_retry_hours", 24)))
        retry = timedelta(hours=float(config.get("failed_retry_hours", 6)))
        threshold = int(config.get("failures_before_backoff", 3))
        # Symbols that keep failing (stale or delisted on Yahoo) while other
        # prices update: retried every failed_retry_hours, listed in
        # reports/price_failures.csv. MIS reference tickers get the same rule.
        self.backoff = FailureBackoff(threshold, retry)
        self.mis_backoff = FailureBackoff(threshold, retry)
        self.mis_reference = (
            bool(config.get("include_mis_reference_prices", True)) if mis_reference is None else mis_reference
        )
        # Assets whose Yahoo symbol could not be resolved are retried once per
        # unresolved_retry_hours instead of every 15 minutes.
        self._unresolved_until = {}

    # Currency ------------------------------------------------------------

    def _partition(self, now):
        from market_data.services.market_price_scope import price_loop_assets

        stale, current, deferred, failing = [], 0, 0, 0
        for asset in price_loop_assets():
            needed, _ = self.freshness.needs_fetch(asset.latest_price_date, asset.latest_fetched_at, now)
            if not needed:
                current += 1
                continue
            retry_at = self._unresolved_until.get(asset.id)
            if retry_at is not None and now < retry_at:
                deferred += 1
                continue
            if self.backoff.deferred(asset.id, now):
                failing += 1
                continue
            stale.append(asset)
        return stale, current, deferred, failing

    @staticmethod
    def _reference_sets():
        """[(name, frozenset of upper-case candidate symbols)] for every MIS reference."""
        from portfolio.mis_report_service import MISReportService

        references, static_symbols = [], set()
        for name, symbols in MISReportService.REFERENCE_SYMBOLS.items():
            candidates = symbols if isinstance(symbols, (tuple, list)) else (symbols,)
            candidates = frozenset(str(symbol).strip().upper() for symbol in candidates if symbol)
            static_symbols.update(candidates)
            references.append((name, candidates))
        for symbol in MISReportService._custom_note_references():
            symbol = str(symbol).strip().upper()
            if symbol not in static_symbols:
                references.append((symbol, frozenset({symbol})))
        return references

    def _mis_references_stale(self, now):
        from django.db.models import OuterRef, Subquery

        from investments.models import Asset
        from market_data.models import MarketPrice

        if self.calendar.in_market_hours(now):
            return True
        session_end = self.calendar.last_session_end(now)
        references = [
            candidates for _, candidates in self._reference_sets()
            if not self.mis_backoff.deferred(candidates, now)
        ]
        all_symbols = set().union(*references) if references else set()
        latest = MarketPrice.objects.filter(asset_id=OuterRef("pk")).order_by("-date", "-id")
        fetched = {
            (row["symbol"] or "").upper(): row["fetched"]
            for row in Asset.objects.filter(family__isnull=True, symbol__in=all_symbols)
            .annotate(fetched=Subquery(latest.values("fetched_at")[:1]))
            .values("symbol", "fetched")
        }
        for candidates in references:
            if not any(
                fetched.get(symbol) is not None and localize(fetched[symbol]) >= session_end
                for symbol in candidates
            ):
                return True
        return False

    def is_current(self, now, *, last_success, at_startup):
        stale, current, deferred, failing = self._partition(now)
        mis_stale = self.mis_reference and self._mis_references_stale(now)
        if stale:
            return False, f"{len(stale)} asset price(s) need a fetch"
        if mis_stale:
            return False, "MIS reference prices need a fetch"
        detail = f"all {current} asset price(s) current"
        if deferred:
            detail += f", {deferred} unresolved symbol(s) waiting for their daily retry"
        if failing:
            detail += f", {failing} repeatedly failing symbol(s) waiting for their retry"
        return True, detail

    # Run ---------------------------------------------------------------------

    def _fetch(self, executor, asset):
        from market_data.services.market_data_manager import MarketDataManager

        if self.deadline_seconds <= 0:
            return MarketDataManager.fetch_and_rebuild(asset=asset)
        return executor.run(lambda: MarketDataManager.fetch_and_rebuild(asset=asset), self.deadline_seconds)

    def run(self, now):
        from config.postgres_advisory_lock import postgres_advisory_lock

        # Shared with update_market_prices / run_scheduled_refresh so a manual
        # refresh and the scheduled one never fetch the same prices at once.
        with postgres_advisory_lock("pwms_refresh_pipeline") as acquired:
            if not acquired:
                raise JobSkipped("another price refresh holds the pwms_refresh_pipeline lock")
            counts = self._run_assets(now)
            if self.mis_reference:
                counts.update(self._run_mis_references(now))
        return counts

    def _run_assets(self, now):
        stale, current, deferred, failing = self._partition(now)
        counts = {
            "assets": len(stale) + current + deferred + failing,
            "fetched": 0,
            "current": current,
            "updated": 0,
            "skipped": 0,
            "failed": 0,
            "timed_out": 0,
            "records": 0,
            "unresolved_deferred": deferred,
            "failing_deferred": failing,
        }
        failures = []
        succeeded, failed = [], {}
        executor = DeadlineExecutor("price-fetch")
        try:
            for index, asset in enumerate(stale):
                if executor.abandoned_count() >= self.max_hung_calls:
                    counts["not_attempted"] = len(stale) - index
                    failures.append(("(cycle)", f"stopped early: {self.max_hung_calls} provider call(s) still hung"))
                    break
                counts["fetched"] += 1
                try:
                    result = self._fetch(executor, asset) or {}
                except DeadlineExceeded:
                    counts["timed_out"] += 1
                    failures.append((asset.name, f"no answer within {self.deadline_seconds:g}s"))
                    logger.debug("Price fetch for %s abandoned after %ss", asset.name, self.deadline_seconds)
                    continue
                except Exception as exc:  # noqa: BLE001 - one asset must not stop the loop
                    counts["failed"] += 1
                    failures.append((asset.name, f"{type(exc).__name__}: {exc}"))
                    failed[asset.id] = self._failure_info(asset, f"{type(exc).__name__}: {exc}")
                    logger.debug("Price fetch for %s raised", asset.name, exc_info=True)
                    continue

                if result.get("success") and not result.get("skipped"):
                    counts["updated"] += 1
                    counts["records"] += int(result.get("records") or 0)
                    self._unresolved_until.pop(asset.id, None)
                    succeeded.append(asset.id)
                    logger.debug("Price updated for %s: %s", asset.name, result.get("current_price"))
                elif result.get("skipped"):
                    counts["skipped"] += 1
                    succeeded.append(asset.id)  # not a provider failure
                    if "symbol" in result and result.get("symbol") is None:
                        self._unresolved_until[asset.id] = now + self.unresolved_retry
                        counts["unresolved"] = counts.get("unresolved", 0) + 1
                    logger.debug("Price skipped for %s: %s", asset.name, result.get("reason"))
                else:
                    counts["failed"] += 1
                    error = str(result.get("error") or result.get("reason") or "unknown error")
                    failures.append((asset.name, error))
                    failed[asset.id] = self._failure_info(asset, error)
                    logger.debug("Price failed for %s: %s", asset.name, result)
        finally:
            executor.close()

        if failures:
            logger.warning(
                "market_prices: %s asset(s) failed or timed out: %s",
                counts["failed"] + counts["timed_out"],
                _summarise(failures),
            )
        newly, changed = self.backoff.record_pass(
            now, succeeded=succeeded, failed=failed, provider_ok=counts["updated"] > 0,
        )
        if changed:
            self._write_failure_report()
        if newly:
            logger.warning(
                "market_prices: %s asset(s) failed %s passes in a row while other prices updated; "
                "retrying them every %gh instead of every %s min: %s. Check the Yahoo symbol or ISIN; "
                "list in reports/price_failures.csv",
                len(newly),
                self.backoff.threshold,
                self.backoff.retry.total_seconds() / 3600,
                self.schedule.minutes,
                "; ".join(
                    f"{entry['info']['name']} ({entry['info']['symbol'] or 'no symbol'})"
                    for _, entry in newly[:MAX_LISTED_FAILURES]
                ) + (f" (+{len(newly) - MAX_LISTED_FAILURES} more)" if len(newly) > MAX_LISTED_FAILURES else ""),
            )
        return counts

    @staticmethod
    def _failure_info(asset, error):
        return {"name": asset.name, "symbol": asset.symbol or "", "isin": asset.isin or "", "error": error}

    def _write_failure_report(self):
        from config.scheduling.files import atomic_write_text, reports_dir

        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(["asset_id", "name", "symbol", "isin", "consecutive_failures", "last_error", "retry_after"])
        for entry in self.backoff.parked():
            info = entry["info"]
            writer.writerow([
                entry["key"], info.get("name", ""), info.get("symbol", ""), info.get("isin", ""),
                entry["count"], info.get("error", ""), localize(entry["until"]).isoformat(timespec="minutes"),
            ])
        path = reports_dir() / "price_failures.csv"
        try:
            atomic_write_text(path, buffer.getvalue())
        except OSError as exc:
            logger.warning("market_prices: could not write %s: %s", path, exc)

    def _run_mis_references(self, now):
        from portfolio.mis_report_service import MISReportService

        if not self._mis_references_stale(now):
            return {"mis_current": 1}
        parked = self.mis_backoff.deferred_keys(now)
        excluded = set().union(*parked) if parked else set()

        def refresh():
            if excluded:
                return MISReportService.refresh_reference_prices(exclude_symbols=excluded)
            return MISReportService.refresh_reference_prices()

        executor = DeadlineExecutor("mis-reference")
        try:
            # deadline_seconds <= 0 runs inline, like the asset fetches.
            result = refresh() if self.deadline_seconds <= 0 else executor.run(
                refresh, max(60.0, self.deadline_seconds * 3)
            )
        except DeadlineExceeded:
            logger.warning("market_prices: MIS reference refresh exceeded its deadline; will retry next cycle.")
            return {"mis_timed_out": 1}
        except Exception as exc:  # noqa: BLE001
            logger.warning("market_prices: MIS reference refresh failed: %s", exc)
            return {"mis_failed": 1}
        finally:
            executor.close()

        failed = {
            frozenset(str(symbol).strip().upper() for symbol in item.get("symbols", ())): item
            for item in result.get("failures", ())
        }
        # Only look up the reference list when there is a streak to update;
        # a clean refresh costs no extra queries.
        succeeded = [
            candidates for _, candidates in self._reference_sets()
            if candidates not in failed and not (candidates and candidates <= excluded)
        ] if (failed or self.mis_backoff.tracking) else []
        newly, _ = self.mis_backoff.record_pass(
            now, succeeded=succeeded, failed=failed, provider_ok=int(result.get("refreshed") or 0) > 0,
        )
        if newly:
            logger.warning(
                "market_prices: %s MIS reference(s) failed %s refreshes in a row; retrying every %gh: %s",
                len(newly),
                self.mis_backoff.threshold,
                self.mis_backoff.retry.total_seconds() / 3600,
                "; ".join(f"{entry['info'].get('name')} ({'/'.join(sorted(key))})" for key, entry in newly),
            )
        return {
            "mis_references": result.get("references", 0),
            "mis_refreshed": result.get("refreshed", 0),
            "mis_failed": result.get("failed", 0),
            "mis_deferred": result.get("deferred", 0),
        }


class _MaxAgeJob(ScheduledJob):
    default_time = "02:00"

    def __init__(self, name):
        config = job_config(name)
        self.hhmm = config.get("time", self.default_time)
        self.max_age_hours = float(config.get("max_age_hours", 24))
        self.min_gap_hours = float(config.get("min_gap_hours", 12))
        super().__init__(name=name, schedule=DailySchedule(self.hhmm), enabled=config.get("enabled", True))

    def is_current(self, now, *, last_success, at_startup):
        return max_age_is_current(
            now, self.hhmm, last_success, at_startup=at_startup,
            max_age_hours=self.max_age_hours, min_gap_hours=self.min_gap_hours,
        )


class SecurityMasterJob(_MaxAgeJob):
    default_time = "02:00"

    def __init__(self):
        super().__init__("security_master")

    def run(self, now):
        from market_data.services.security_master_generator import SecurityMasterGenerator
        from market_data.services.security_resolver import SecurityResolver

        result = SecurityMasterGenerator.generate()
        SecurityResolver.reload_if_changed()
        return {key: result.get(key) for key in ("total", "resolved", "unresolved", "non_yahoo")}


class SecurityMetricsJob(_MaxAgeJob):
    default_time = "02:30"

    def __init__(self):
        super().__init__("security_metrics")

    def run(self, now):
        from market_data.services.security_metrics_refresh import refresh_security_metrics

        return refresh_security_metrics()
