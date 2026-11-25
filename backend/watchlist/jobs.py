"""Watch List refresh, right after the nightly MF Intelligence sync.

Runs the same steps as before: shared benchmark history, the background
universe/PMS refresh, then the AMFI universe and performance refresh that
run_scheduled_refresh also performs.
"""
import logging

from config.pwms_config import get as get_pwms_config
from config.scheduling.framework import ScheduledJob, slot_is_current
from config.scheduling.registry import job_config

logger = logging.getLogger("watchlist.jobs")


class WatchListJob(ScheduledJob):
    def __init__(self):
        config = job_config("watchlist")
        self.parent_hhmm = get_pwms_config("mf_intelligence", "auto_sync_time", "01:00")
        super().__init__(
            name="watchlist", schedule=None, after="mf_intelligence",
            requires_parent_success=False, enabled=config.get("enabled", True),
        )

    def is_current(self, now, *, last_success, at_startup):
        return slot_is_current(now, self.parent_hhmm, last_success)

    def run(self, now):
        from watchlist.services.background_refresh import BackgroundWatchListRefreshService
        from watchlist.services.benchmark import BenchmarkPerformanceService
        from watchlist.services.performance import AMFIPerformanceService
        from watchlist.services.universe import AMFIUniverseService

        steps = (
            ("benchmark_history", BenchmarkPerformanceService.ensure_benchmark_master_history),
            ("background_refresh", BackgroundWatchListRefreshService.refresh),
            ("amfi_universe", AMFIUniverseService.refresh),
            ("amfi_performance", AMFIPerformanceService.refresh),
        )
        results, failures = {}, []
        for name, step in steps:
            try:
                results[name] = step() or {}
            except Exception as exc:  # noqa: BLE001 - later steps still run
                failures.append(f"{name}: {type(exc).__name__}: {exc}")
                logger.debug("Watch List step %s failed", name, exc_info=True)
        if len(failures) == len(steps):
            raise RuntimeError("; ".join(failures))
        if failures:
            logger.warning("watchlist: %s step(s) failed: %s", len(failures), "; ".join(failures))
        universe = results.get("amfi_universe") or {}
        performance = results.get("amfi_performance") or {}
        return {
            "failed_steps": len(failures),
            "universe_discovered": universe.get("discovered", 0) if isinstance(universe, dict) else 0,
            "performance_updated": performance.get("metrics_updated", 0) if isinstance(performance, dict) else 0,
        }
