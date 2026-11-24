"""Fetch CharlieAlpha / AMFI data and persist it.

Network calls run on a small worker pool that shares one rate limiter;
every database write happens on the calling thread in a short
transaction guarded by DATABASE_SCHEDULER_LOCK, so SQLite never sees
concurrent writers and no lock is held while waiting on the network.
"""

import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from config.database_scheduler_lock import DATABASE_SCHEDULER_LOCK
from config.pwms_config import get as get_pwms_config
from mf_intelligence.constants import HORIZONS, METRIC_FIELDS, PLAN_REGULAR
from mf_intelligence.models import (
    MFAumHistory,
    MFBenchmark,
    MFBenchmarkMonthly,
    MFCategory,
    MFCategoryMedian,
    MFHolding,
    MFHoldingSnapshot,
    MFNavMonthly,
    MFScheme,
    MFSchemeMetric,
    MFStockClassification,
    MFSyncRun,
)
from mf_intelligence.services import cap_list
from mf_intelligence.services.client import CharlieAlphaClient, CharlieAlphaNotFound
from mf_intelligence.services.returns import ValueSeries, compute_return_profile, quantize, to_date, to_decimal
from mf_intelligence.services.universe import sync_target_schemes


logger = logging.getLogger(__name__)

# One sync at a time per process (UI button, scheduler and command).
SYNC_LOCK = threading.Lock()

STEP_NAV = "nav"
STEP_HOLDINGS = "holdings"
STEP_AUM = "aum"
STEP_BENCHMARKS = "benchmarks"
STEP_CAP_LIST = "cap_list"
ALL_STEPS = (STEP_NAV, STEP_HOLDINGS, STEP_AUM, STEP_BENCHMARKS, STEP_CAP_LIST)
MAX_STORED_ERRORS = 200


def _config(key, default):
    return get_pwms_config("mf_intelligence", key, default)


def _to_decimal(value):
    decimal = to_decimal(value)
    return quantize(decimal) if decimal is not None else None


def _clean_text(value, length):
    return str(value or "").strip()[:length]


def _to_int(value):
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _fresh(timestamp, max_age):
    return timestamp is not None and timezone.now() - timestamp < max_age


class MFIntelligenceSync:
    def __init__(self, client=None, workers=None, progress=None):
        self.client = client or CharlieAlphaClient()
        self.workers = max(1, int(workers or _config("fetch_workers", 4)))
        self.progress = progress or (lambda message: None)
        self.run_record = None
        self.stats = {}
        self.errors = []

    # Bookkeeping -------------------------------------------------------

    def _bump(self, key, amount=1):
        self.stats[key] = self.stats.get(key, 0) + amount

    def _error(self, context, exc):
        logger.warning("MF Intelligence sync: %s: %s", context, exc)
        self._bump("errors")
        if len(self.errors) < MAX_STORED_ERRORS:
            self.errors.append(f"{context}: {exc}")

    def _step(self, text):
        self.progress(text)
        if self.run_record is not None:
            self.run_record.current_step = text[:200]
            self._save_run(["current_step"])

    def _save_run(self, extra_fields=()):
        self.stats["requests"] = self.client.request_count
        self.run_record.stats = dict(self.stats)
        self.run_record.errors = list(self.errors)
        with DATABASE_SCHEDULER_LOCK:
            self.run_record.save(update_fields=["stats", "errors", *extra_fields])

    @staticmethod
    def close_interrupted_runs(exclude_id=None):
        """Fail runs left 'running' by a process that died mid-sync.

        A live sync updates its row every few seconds, so a running row
        untouched for longer than the stale window cannot still be alive.
        """
        stale_before = timezone.now() - timedelta(hours=_config("stale_run_hours", 6))
        with DATABASE_SCHEDULER_LOCK:
            MFSyncRun.objects.filter(status=MFSyncRun.STATUS_RUNNING, started_at__lt=stale_before).exclude(
                pk=exclude_id
            ).update(
                status=MFSyncRun.STATUS_FAILED,
                finished_at=timezone.now(),
                current_step="Interrupted before it finished",
            )

    # Orchestration -------------------------------------------------------

    def run(self, categories=None, plans=(PLAN_REGULAR,), force=False, steps=ALL_STEPS, limit=None,
            trigger="manual", user=None, run_record=None):
        """Run a sync. ``categories`` is a list of slugs (None = all)."""
        steps = tuple(steps)
        scope = {
            "categories": list(categories) if categories else "all",
            "plans": list(plans),
            "force": bool(force),
            "steps": list(steps),
            "limit": limit,
        }
        if run_record is None:
            run_record = MFSyncRun.objects.create(trigger=trigger, triggered_by=user, scope=scope)
        else:
            run_record.scope = scope
            run_record.save(update_fields=["scope"])
        self.run_record = run_record
        self.close_interrupted_runs(exclude_id=run_record.pk)
        try:
            self._step("Syncing category catalogue")
            self.sync_categories()
            self._step("Syncing scheme master")
            self.sync_scheme_master()

            selected = MFCategory.objects.all()
            if categories:
                selected = selected.filter(slug__in=list(categories))
            selected = list(selected)
            missing = set(categories or []) - {category.slug for category in selected}
            for slug in sorted(missing):
                self._error(f"category {slug}", "not found in the CharlieAlpha catalogue")

            for index, category in enumerate(selected, start=1):
                self._step(f"Metrics {index}/{len(selected)}: {category.name}")
                self.sync_category_metrics(category)
            fund_steps = [step for step in steps if step in (STEP_NAV, STEP_HOLDINGS, STEP_AUM)]
            if fund_steps:
                self.sync_funds(selected, plans, fund_steps, force=force, limit=limit)
            if STEP_BENCHMARKS in steps:
                self._step("Syncing benchmarks")
                self.sync_benchmarks(force=force)
            if STEP_CAP_LIST in steps:
                self._step("Syncing market-cap classification")
                self.sync_cap_classification(force=force)
            run_record.status = MFSyncRun.STATUS_PARTIAL if self.errors else MFSyncRun.STATUS_SUCCESS
            run_record.current_step = "Completed"
        except Exception as exc:  # noqa: BLE001 - recorded on the run, re-raised for callers
            self._error("sync aborted", exc)
            run_record.status = MFSyncRun.STATUS_FAILED
            run_record.current_step = "Failed"
            run_record.finished_at = timezone.now()
            self._save_run(["status", "current_step", "finished_at"])
            raise
        run_record.finished_at = timezone.now()
        self._save_run(["status", "current_step", "finished_at"])
        return run_record

    # Catalogue -------------------------------------------------------------

    def sync_categories(self):
        payload = self.client.categories()
        groups = payload.get("categories") or {}
        order = 0
        with DATABASE_SCHEDULER_LOCK, transaction.atomic():
            for asset_class, items in groups.items():
                for item in items or []:
                    order += 1
                    slug = _clean_text(item.get("slug"), 80)
                    if not slug:
                        continue
                    MFCategory.objects.update_or_create(
                        slug=slug,
                        defaults={
                            "name": _clean_text(item.get("name"), 200) or slug,
                            "asset_class": _clean_text(asset_class, 50),
                            "display_order": order,
                        },
                    )
        self._bump("categories", order)

    def sync_scheme_master(self):
        payload = self.client.schemes()
        category_by_slug = {category.slug: category for category in MFCategory.objects.all()}
        incoming = {}
        category_totals = {}
        for group in payload.get("categories") or []:
            slug = group.get("category_slug")
            category = category_by_slug.get(slug)
            if category is None and slug:
                with DATABASE_SCHEDULER_LOCK:
                    category, _ = MFCategory.objects.get_or_create(
                        slug=_clean_text(slug, 80),
                        defaults={
                            "name": _clean_text(group.get("category"), 200) or slug,
                            "asset_class": _clean_text(group.get("asset_class"), 50),
                            "display_order": len(category_by_slug) + 1,
                        },
                    )
                category_by_slug[slug] = category
            if category is None:
                continue
            category_totals[category.pk] = _to_int(group.get("total_schemes")) or len(group.get("schemes") or [])
            for scheme in group.get("schemes") or []:
                code = _clean_text(scheme.get("code"), 20)
                if code:
                    incoming[code] = (category, scheme)
        if not incoming:
            raise ValueError("CharlieAlpha returned an empty scheme master")

        existing = {scheme.amfi_code: scheme for scheme in MFScheme.objects.all()}
        to_create, to_update = [], []
        fields = ("name", "amc", "plan", "option", "category_id", "is_active")
        for code, (category, row) in incoming.items():
            values = {
                "name": _clean_text(row.get("name"), 500) or code,
                "amc": _clean_text(row.get("amc"), 300),
                "plan": _clean_text(row.get("plan"), 20),
                "option": _clean_text(row.get("option"), 20),
                "category_id": category.pk,
                "is_active": True,
            }
            scheme = existing.get(code)
            if scheme is None:
                to_create.append(MFScheme(amfi_code=code, **values))
                continue
            if any(getattr(scheme, field) != value for field, value in values.items()):
                for field, value in values.items():
                    setattr(scheme, field, value)
                to_update.append(scheme)
        retired = [code for code, scheme in existing.items() if code not in incoming and scheme.is_active]
        now = timezone.now()
        with DATABASE_SCHEDULER_LOCK, transaction.atomic():
            MFScheme.objects.bulk_create(to_create, batch_size=500)
            MFScheme.objects.bulk_update(to_update, list(fields), batch_size=500)
            if retired:
                MFScheme.objects.filter(amfi_code__in=retired).update(is_active=False)
            for category_id, total in category_totals.items():
                MFCategory.objects.filter(pk=category_id).update(total_schemes=total, funds_synced_at=now)
        self._bump("schemes_created", len(to_create))
        self._bump("schemes_updated", len(to_update))
        self._bump("schemes_retired", len(retired))

    # Category metrics --------------------------------------------------------

    def sync_category_metrics(self, category):
        try:
            payload = self.client.metrics_by_category(category.slug)
        except Exception as exc:  # noqa: BLE001
            self._error(f"metrics {category.slug}", exc)
            return
        rows = payload.get("schemes") or []
        schemes = {scheme.amfi_code: scheme for scheme in MFScheme.objects.filter(amfi_code__in=[
            _clean_text(row.get("code"), 20) for row in rows
        ])}
        now = timezone.now()
        scheme_fields = (
            "slug", "data_as_of", "age_label", "age_months", "avg_aum_crore", "aum_period",
            "ter", "ter_date", "all_time_max_drawdown", "metrics_synced_at",
        )
        to_update = []
        metric_values = {}
        latest_as_of = None
        for row in rows:
            code = _clean_text(row.get("code"), 20)
            scheme = schemes.get(code)
            if scheme is None:
                scheme = MFScheme(
                    amfi_code=code,
                    name=_clean_text(row.get("name"), 500) or code,
                    amc=_clean_text(row.get("amc_name"), 300),
                    plan=_clean_text(row.get("plan"), 20),
                    option=_clean_text(row.get("option"), 20),
                    category=category,
                )
                with DATABASE_SCHEDULER_LOCK:
                    scheme.save()
                schemes[code] = scheme
            as_of = to_date(row.get("data_as_of"))
            latest_as_of = max(filter(None, [latest_as_of, as_of]), default=None)
            scheme.slug = _clean_text(row.get("slug"), 500)
            scheme.data_as_of = as_of
            scheme.age_label = _clean_text(row.get("age"), 50)
            scheme.age_months = _to_decimal(row.get("age_months"))
            scheme.avg_aum_crore = _to_decimal(row.get("avg_aum_crore"))
            scheme.aum_period = _clean_text(row.get("aum_period"), 100)
            scheme.ter = _to_decimal(row.get("ter"))
            scheme.ter_date = to_date(row.get("ter_date"))
            scheme.all_time_max_drawdown = _to_decimal(row.get("all_time_max_drawdown"))
            scheme.metrics_synced_at = now
            to_update.append(scheme)
            for horizon in HORIZONS:
                values = (row.get("metrics") or {}).get(horizon) or {}
                metric_values[(scheme.pk, horizon)] = (as_of, {field: _to_decimal(values.get(field)) for field in METRIC_FIELDS})

        existing_metrics = {
            (metric.scheme_id, metric.horizon): metric
            for metric in MFSchemeMetric.objects.filter(scheme_id__in=[scheme.pk for scheme in to_update])
        }
        create_metrics, update_metrics = [], []
        for (scheme_id, horizon), (as_of, values) in metric_values.items():
            metric = existing_metrics.get((scheme_id, horizon)) or MFSchemeMetric(scheme_id=scheme_id, horizon=horizon)
            metric.as_of = as_of
            for field, value in values.items():
                setattr(metric, field, value)
            (update_metrics if metric.pk else create_metrics).append(metric)

        medians = payload.get("category_medians") or {}
        with DATABASE_SCHEDULER_LOCK, transaction.atomic():
            MFScheme.objects.bulk_update(to_update, list(scheme_fields), batch_size=500)
            MFSchemeMetric.objects.bulk_create(create_metrics, batch_size=500)
            MFSchemeMetric.objects.bulk_update(update_metrics, ["as_of", *METRIC_FIELDS], batch_size=500)
            for horizon in HORIZONS:
                values = (medians.get("metrics") or {}).get(horizon) or {}
                counts = ((medians.get("sample_counts") or {}).get("metrics") or {}).get(horizon) or {}
                MFCategoryMedian.objects.update_or_create(
                    category=category,
                    horizon=horizon,
                    defaults={
                        "as_of": latest_as_of,
                        "values": {field: values.get(field) for field in METRIC_FIELDS},
                        "sample_counts": {field: counts.get(field) for field in METRIC_FIELDS},
                    },
                )
            category.peer_count = _to_int(medians.get("peer_count"))
            category.median_all_time_max_drawdown = _to_decimal(medians.get("all_time_max_drawdown"))
            category.metrics_as_of = latest_as_of
            category.metrics_synced_at = now
            category.save(update_fields=[
                "peer_count", "median_all_time_max_drawdown", "metrics_as_of", "metrics_synced_at", "updated_at",
            ])
        self._bump("metric_schemes", len(to_update))

    # Per-fund NAV / holdings / AUM ------------------------------------------

    def _needs(self, scheme, step, force):
        if force:
            return True
        if step == STEP_NAV:
            return scheme.nav_synced_at is None or timezone.localtime(scheme.nav_synced_at).date() < timezone.localdate()
        if step == STEP_HOLDINGS:
            return not _fresh(scheme.holdings_synced_at, timedelta(days=_config("holdings_refresh_days", 7)))
        if step == STEP_AUM:
            return not _fresh(scheme.aum_synced_at, timedelta(days=_config("aum_refresh_days", 30)))
        return False

    def _fetch(self, scheme_code, step):
        if step == STEP_NAV:
            return self.client.nav_history(scheme_code)
        if step == STEP_HOLDINGS:
            return self.client.holdings(scheme_code, change=True)
        return self.client.aum_history(scheme_code)

    def sync_funds(self, categories, plans, steps, force=False, limit=None):
        targets = []
        for category in categories:
            targets.extend(sync_target_schemes(category, plans))
        if limit:
            targets = targets[: int(limit)]
        jobs = [(scheme, step) for scheme in targets for step in steps if self._needs(scheme, step, force)]
        self.stats["fund_schemes"] = len(targets)
        self.stats["fund_requests_planned"] = len(jobs)
        total = len(jobs)
        if not total:
            return
        self._step(f"Fetching NAV / holdings / AUM: 0/{total}")
        done = 0
        with ThreadPoolExecutor(max_workers=self.workers, thread_name_prefix="mfi-fetch") as pool:
            futures = {pool.submit(self._fetch, scheme.amfi_code, step): (scheme, step) for scheme, step in jobs}
            for future in as_completed(futures):
                scheme, step = futures[future]
                done += 1
                try:
                    payload = future.result()
                    self._store(scheme, step, payload)
                except CharlieAlphaNotFound:
                    # No data published yet (new fund) - remember the check.
                    self._mark_checked(scheme, step)
                    self._bump(f"{step}_not_found")
                except Exception as exc:  # noqa: BLE001
                    self._error(f"{step} {scheme.amfi_code}", exc)
                if done % 25 == 0 or done == total:
                    self._step(f"Fetching NAV / holdings / AUM: {done}/{total}")

    def _store(self, scheme, step, payload):
        if step == STEP_NAV:
            self.store_nav(scheme, payload)
        elif step == STEP_HOLDINGS:
            self.store_holdings(scheme, payload)
        else:
            self.store_aum(scheme, payload)
        self._bump(f"{step}_updated")

    def _mark_checked(self, scheme, step):
        field = {STEP_NAV: "nav_synced_at", STEP_HOLDINGS: "holdings_synced_at", STEP_AUM: "aum_synced_at"}[step]
        with DATABASE_SCHEDULER_LOCK:
            MFScheme.objects.filter(pk=scheme.pk).update(**{field: timezone.now()})

    def store_nav(self, scheme, payload):
        series = ValueSeries.from_arrays(payload.get("dates"), payload.get("nav"))
        now = timezone.now()
        if not len(series):
            self._mark_checked(scheme, STEP_NAV)
            return
        profile = compute_return_profile(series)
        updates = {
            "latest_nav": quantize(series.last_value) if series.last_value < 10**17 else None,
            "latest_nav_date": series.last_date,
            "first_nav_date": series.first_date,
            "nav_synced_at": now,
            **profile,
        }
        for key, field in (("ter", "ter"), ("avg_aum_crore", "avg_aum_crore")):
            value = _to_decimal(payload.get(key))
            if value is not None:
                updates[field] = value
        if payload.get("ter_date"):
            updates["ter_date"] = to_date(payload.get("ter_date"))
        if payload.get("aum_period"):
            updates["aum_period"] = _clean_text(payload.get("aum_period"), 100)

        monthly = series.monthly_points()
        existing = {item.month: item for item in MFNavMonthly.objects.filter(scheme=scheme)}
        to_create, to_update = [], []
        for month, nav_date, nav in monthly:
            value = quantize(nav)
            item = existing.get(month)
            if item is None:
                to_create.append(MFNavMonthly(scheme=scheme, month=month, nav_date=nav_date, nav=value))
            elif item.nav_date != nav_date or item.nav != value:
                item.nav_date, item.nav = nav_date, value
                to_update.append(item)
        with DATABASE_SCHEDULER_LOCK, transaction.atomic():
            MFScheme.objects.filter(pk=scheme.pk).update(**updates)
            MFNavMonthly.objects.bulk_create(to_create, batch_size=1000)
            MFNavMonthly.objects.bulk_update(to_update, ["nav_date", "nav"], batch_size=1000)

    def store_holdings(self, scheme, payload):
        as_of = to_date(payload.get("as_of"))
        if as_of is None or not payload.get("holdings"):
            self._mark_checked(scheme, STEP_HOLDINGS)
            return
        allocation = payload.get("asset_allocation") or {}
        physical = allocation.get("physical") or {}
        derivatives = allocation.get("derivatives") or {}
        summary = payload.get("comparison_summary") or {}
        defaults = {
            "prev_as_of": to_date(payload.get("prev_as_of")),
            "total_holdings": _to_int(payload.get("total_holdings")) or len(payload.get("holdings") or []),
            "equity_pct": _to_decimal(physical.get("equity_pct")),
            "debt_pct": _to_decimal(physical.get("debt_pct")),
            "fund_unit_pct": _to_decimal(physical.get("fund_unit_pct")),
            "commodity_pct": _to_decimal(physical.get("commodity_pct")),
            "cash_pct": _to_decimal(physical.get("cash_pct")),
            "total_pct": _to_decimal(physical.get("total_pct")),
            "derivatives_gross_pct": _to_decimal(derivatives.get("gross_exposure_pct")),
            "derivatives_net_pct": _to_decimal(derivatives.get("net_exposure_pct")),
            "derivatives_count": _to_int(derivatives.get("count")),
            "net_equity_exposure_pct": _to_decimal(allocation.get("net_equity_exposure_pct")),
            "new_additions_count": _to_int(summary.get("new_additions_count")),
            "complete_exits_count": _to_int(summary.get("complete_exits_count")),
            "increased_count": _to_int(summary.get("increased_count")),
            "decreased_count": _to_int(summary.get("decreased_count")),
            "unchanged_count": _to_int(summary.get("unchanged_count")),
        }

        def build(row, snapshot, is_exit=False):
            return MFHolding(
                snapshot=snapshot,
                isin=_clean_text(row.get("isin"), 20).upper(),
                name=_clean_text(row.get("name"), 500) or "Unnamed holding",
                industry=_clean_text(row.get("industry"), 200),
                holding_type=_clean_text(row.get("holding_type"), 30).lower(),
                position_side=_clean_text(row.get("position_side"), 10).lower(),
                holding_pct=None if is_exit else _to_decimal(row.get("holding_pct")),
                quantity=_to_decimal(row.get("quantity")),
                prev_holding_pct=_to_decimal(row.get("prev_holding_pct")),
                change_pct=_to_decimal(row.get("change_pct")),
                status=_clean_text(row.get("status") or ("EXITED" if is_exit else ""), 20).upper(),
                is_exit=is_exit,
            )

        with DATABASE_SCHEDULER_LOCK, transaction.atomic():
            snapshot, _ = MFHoldingSnapshot.objects.update_or_create(scheme=scheme, as_of=as_of, defaults=defaults)
            snapshot.holdings.all().delete()
            rows = [build(row, snapshot) for row in payload.get("holdings") or []]
            rows += [build(row, snapshot, is_exit=True) for row in payload.get("exits") or []]
            MFHolding.objects.bulk_create(rows, batch_size=1000)
            scheme_updates = {"holdings_synced_at": timezone.now()}
            isin = _clean_text(payload.get("isin"), 20).upper()
            if isin:
                scheme_updates["isin"] = isin
            MFScheme.objects.filter(pk=scheme.pk).update(**scheme_updates)

    def store_aum(self, scheme, payload):
        history = payload.get("history") or []
        existing = {item.as_of_date: item for item in MFAumHistory.objects.filter(scheme=scheme)}
        to_create, to_update = [], []
        for row in history:
            as_of = to_date(row.get("as_of_date"))
            value = _to_decimal(row.get("avg_aum_crore"))
            if as_of is None:
                continue
            period = _clean_text(row.get("period"), 100)
            item = existing.get(as_of)
            if item is None:
                to_create.append(MFAumHistory(scheme=scheme, as_of_date=as_of, period=period, avg_aum_crore=value))
            elif item.avg_aum_crore != value or item.period != period:
                item.avg_aum_crore, item.period = value, period
                to_update.append(item)
        with DATABASE_SCHEDULER_LOCK, transaction.atomic():
            MFAumHistory.objects.bulk_create(to_create, batch_size=500)
            MFAumHistory.objects.bulk_update(to_update, ["avg_aum_crore", "period"], batch_size=500)
            MFScheme.objects.filter(pk=scheme.pk).update(aum_synced_at=timezone.now())

    # Benchmarks ------------------------------------------------------------

    def sync_benchmarks(self, force=False):
        try:
            payload = self.client.benchmarks()
        except Exception as exc:  # noqa: BLE001
            self._error("benchmark list", exc)
            return
        for item in payload.get("benchmarks") or []:
            slug = _clean_text(item.get("slug"), 120)
            if not slug:
                continue
            with DATABASE_SCHEDULER_LOCK:
                benchmark, _ = MFBenchmark.objects.update_or_create(
                    slug=slug,
                    defaults={"name": _clean_text(item.get("name"), 200) or slug, "asset_type": _clean_text(item.get("type"), 50)},
                )
            if (
                not force
                and benchmark.synced_at is not None
                and timezone.localtime(benchmark.synced_at).date() >= timezone.localdate()
            ):
                continue
            try:
                history = self.client.benchmark_history(slug)
            except Exception as exc:  # noqa: BLE001
                self._error(f"benchmark {slug}", exc)
                continue
            self.store_benchmark(benchmark, history)
            self._bump("benchmarks_updated")

    def store_benchmark(self, benchmark, payload):
        series = ValueSeries.from_arrays(payload.get("dates"), payload.get("nav"))
        if not len(series):
            return
        profile = compute_return_profile(series)
        profile.pop("return_since_inception", None)
        existing = {item.month: item for item in MFBenchmarkMonthly.objects.filter(benchmark=benchmark)}
        to_create, to_update = [], []
        for month, value_date, value in series.monthly_points():
            value = quantize(value)
            item = existing.get(month)
            if item is None:
                to_create.append(MFBenchmarkMonthly(benchmark=benchmark, month=month, value_date=value_date, value=value))
            elif item.value_date != value_date or item.value != value:
                item.value_date, item.value = value_date, value
                to_update.append(item)
        with DATABASE_SCHEDULER_LOCK, transaction.atomic():
            MFBenchmark.objects.filter(pk=benchmark.pk).update(
                start_date=series.first_date,
                latest_date=series.last_date,
                latest_value=quantize(series.last_value),
                synced_at=timezone.now(),
                **profile,
            )
            MFBenchmarkMonthly.objects.bulk_create(to_create, batch_size=1000)
            MFBenchmarkMonthly.objects.bulk_update(to_update, ["value_date", "value"], batch_size=1000)

    # Market-cap classification ---------------------------------------------

    def sync_cap_classification(self, force=False):
        try:
            result = cap_list.refresh_amfi_classification(force=force)
        except Exception as exc:  # noqa: BLE001
            self._error("AMFI market-cap list", exc)
            result = {"status": "unavailable"}
        self.stats["cap_list"] = result.get("status")
        if result.get("status") in ("imported", "current"):
            return
        proxy_age = (
            MFStockClassification.objects.filter(source=MFStockClassification.SOURCE_INDEX_PROXY)
            .order_by("-updated_at")
            .values_list("updated_at", flat=True)
            .first()
        )
        if proxy_age and not force and _fresh(proxy_age, timedelta(days=30)):
            return
        try:
            proxy = cap_list.build_index_proxy(self.client)
            self.stats["cap_list"] = f"index_proxy:{proxy.get('status')}"
        except Exception as exc:  # noqa: BLE001
            self._error("index proxy classification", exc)
