import logging
import threading

from django.db import close_old_connections
from django.shortcuts import get_object_or_404
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from mf_intelligence.constants import DEFAULT_HORIZON, HORIZONS, PLAN_CHOICES
from mf_intelligence.models import MFCategory, MFScheme, MFSyncRun
from mf_intelligence.services.dashboard import (
    build_category_dashboard,
    build_scheme_detail,
    categories_payload,
    sync_run_payload,
)
from mf_intelligence.services.sync import ALL_STEPS, SYNC_LOCK, MFIntelligenceSync
from mf_intelligence.services.universe import normalise_plan
from users.permissions import CanWriteFinancialData


logger = logging.getLogger(__name__)


def _horizon(request):
    value = (request.query_params.get("horizon") or DEFAULT_HORIZON).upper()
    return value if value in HORIZONS else DEFAULT_HORIZON


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def mf_intelligence_categories(request):
    return Response(categories_payload())


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def mf_intelligence_dashboard(request, slug):
    category = get_object_or_404(MFCategory, slug=slug)
    payload = build_category_dashboard(
        category,
        plan=normalise_plan(request.query_params.get("plan")),
        horizon=_horizon(request),
        benchmark_slug=(request.query_params.get("benchmark") or "").strip() or None,
    )
    return Response(payload)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def mf_intelligence_scheme(request, code):
    scheme = get_object_or_404(MFScheme.objects.select_related("category"), amfi_code=code)
    payload = build_scheme_detail(
        scheme,
        benchmark_slug=(request.query_params.get("benchmark") or "").strip() or None,
    )
    return Response(payload)


def _run_sync(run_id, categories, plans, force):
    try:
        run = MFSyncRun.objects.get(pk=run_id)
        MFIntelligenceSync().run(
            categories=categories,
            plans=plans,
            force=force,
            steps=ALL_STEPS,
            trigger=run.trigger,
            user=run.triggered_by,
            run_record=run,
        )
    except Exception:  # noqa: BLE001 - the run row already records the failure
        logger.exception("MF Intelligence background sync failed")
    finally:
        SYNC_LOCK.release()
        close_old_connections()


@api_view(["POST"])
@permission_classes([IsAuthenticated, CanWriteFinancialData])
def mf_intelligence_sync(request):
    body = request.data if isinstance(request.data, dict) else {}
    slug = (body.get("category") or "").strip()
    if slug and not MFCategory.objects.filter(slug=slug).exists():
        return Response({"detail": "Unknown category."}, status=400)
    plan_value = (body.get("plan") or "Regular").strip().lower()
    if plan_value == "both":
        plans = list(PLAN_CHOICES)
    elif plan_value in {plan.lower() for plan in PLAN_CHOICES}:
        plans = [normalise_plan(plan_value)]
    else:
        return Response({"detail": "plan must be Regular, Direct or Both."}, status=400)
    force = bool(body.get("force"))

    if not SYNC_LOCK.acquire(blocking=False):
        running = MFSyncRun.objects.filter(status=MFSyncRun.STATUS_RUNNING).first()
        return Response({"status": "already_running", "run": sync_run_payload(running)}, status=202)
    try:
        run = MFSyncRun.objects.create(
            trigger="manual",
            triggered_by=request.user,
            scope={"categories": [slug] if slug else "all", "plans": plans, "force": force},
            current_step="Queued",
        )
        threading.Thread(
            target=_run_sync,
            args=(run.pk, [slug] if slug else None, plans, force),
            name="mf-intelligence-sync",
            daemon=True,
        ).start()
    except Exception:
        SYNC_LOCK.release()
        raise
    return Response({"status": "started", "run": sync_run_payload(run)}, status=202)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def mf_intelligence_sync_status(request):
    run = MFSyncRun.objects.order_by("-started_at").first()
    return Response({"running": SYNC_LOCK.locked(), "run": sync_run_payload(run)})
