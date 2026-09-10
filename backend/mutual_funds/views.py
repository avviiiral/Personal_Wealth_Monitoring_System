from decimal import Decimal

from django.views.decorators.csrf import ensure_csrf_cookie
from django.db.models import Sum

from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from mutual_funds.models import (
    MutualFundHolding,
    MutualFundTransaction,
    MutualFundScheme,
    SIP,
    SIPInstallment,
    SIPInstallmentStatus,
)
from .serializers import (
    MutualFundHoldingSerializer,
    MutualFundTransactionSerializer,
    MutualFundSchemeSerializer,
    CreateMutualFundTransactionSerializer,
    CreateSIPSerializer,
    SIPSerializer,
)
from .services.sip_engine import SIPEngine
from .services.holding_engine import MutualFundHoldingEngine
from .services.sip_summary import SIPSummaryService
from .services.sip_installment_execution import SIPInstallmentExecutionService
from .services.sip_installments import SIPInstallmentService
from users.permissions import get_active_family_group_id


def _active_family(request):
    family_id = get_active_family_group_id(request.user)
    if family_id is None:
        return None
    return family_id


@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated])
def mutual_fund_schemes(request):
    family_id = _active_family(request)
    if family_id is None:
        return Response({"error": "A valid active family is required."}, status=400)

    if request.method == "GET":
        search = request.query_params.get("search", "").strip()
        schemes = MutualFundScheme.objects.filter(
            family_group_id=family_id,
            is_active=True,
        )
        if search:
            from django.db.models import Q
            schemes = schemes.filter(
                Q(scheme_name__icontains=search)
                | Q(scheme_code__icontains=search)
                | Q(isin_growth__icontains=search)
                | Q(isin_dividend__icontains=search)
                | Q(amc_name__icontains=search)
            )
        schemes = schemes.order_by("scheme_name")[:50]
        serializer = MutualFundSchemeSerializer(schemes, many=True)
        return Response({"count": len(serializer.data), "results": serializer.data})

    serializer = MutualFundSchemeSerializer(
        data=request.data,
        context={"request": request},
    )
    serializer.is_valid(raise_exception=True)
    scheme = serializer.save(owner=request.user, family_group_id=family_id)
    return Response(MutualFundSchemeSerializer(scheme).data, status=201)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def mutual_fund_transaction_create(request):
    family_id = _active_family(request)
    if family_id is None:
        return Response({"error": "A valid active family is required."}, status=400)

    serializer = CreateMutualFundTransactionSerializer(
        data=request.data,
        context={"request": request},
    )
    serializer.is_valid(raise_exception=True)
    transaction_record = serializer.save(owner=request.user, family_group_id=family_id)
    MutualFundHoldingEngine.rebuild_holding(
        transaction_record.scheme,
        family_group_id=family_id,
    )
    return Response(MutualFundTransactionSerializer(transaction_record).data, status=201)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
@ensure_csrf_cookie
def sip_create(request):
    family_id = _active_family(request)
    if family_id is None:
        return Response({"error": "A valid active family is required."}, status=400)

    serializer = CreateSIPSerializer(data=request.data, context={"request": request})
    serializer.is_valid(raise_exception=True)
    sip = serializer.save(owner=request.user, family_group_id=family_id)
    SIPInstallmentService.synchronize_sip(sip)
    return Response(SIPSerializer(sip).data, status=201)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def mutual_fund_summary(request):
    family_id = _active_family(request)
    if family_id is None:
        return Response({"error": "A valid active family is required."}, status=400)

    holdings = MutualFundHolding.objects.filter(
        family_group_id=family_id,
        scheme__is_active=True,
    )
    totals = holdings.aggregate(
        invested=Sum("invested_value"),
        current=Sum("current_value"),
        pnl=Sum("unrealized_pnl"),
    )
    invested = totals["invested"] or Decimal("0")
    current = totals["current"] or Decimal("0")
    pnl = totals["pnl"] or Decimal("0")
    pnl_percentage = (pnl / invested) * Decimal("100") if invested else Decimal("0")
    return Response({
        "total_invested": invested,
        "total_current_value": current,
        "total_unrealized_pnl": pnl,
        "pnl_percentage": round(float(pnl_percentage), 2),
        "number_of_holdings": holdings.count(),
    })


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def mutual_fund_holdings(request):
    family_id = _active_family(request)
    if family_id is None:
        return Response({"error": "A valid active family is required."}, status=400)
    holdings = MutualFundHolding.objects.filter(
        family_group_id=family_id,
        scheme__is_active=True,
    ).select_related("scheme").order_by("-current_value")
    serializer = MutualFundHoldingSerializer(holdings, many=True)
    return Response({"count": holdings.count(), "results": serializer.data})


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def mutual_fund_transactions(request):
    family_id = _active_family(request)
    if family_id is None:
        return Response({"error": "A valid active family is required."}, status=400)
    transactions = MutualFundTransaction.objects.filter(
        family_group_id=family_id,
    ).select_related("scheme").order_by("-transaction_date", "-created_at")
    serializer = MutualFundTransactionSerializer(transactions, many=True)
    return Response({"count": transactions.count(), "results": serializer.data})


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def sip_list(request):
    family_id = _active_family(request)
    if family_id is None:
        return Response({"error": "A valid active family is required."}, status=400)
    sips = SIP.objects.filter(
        family_group_id=family_id,
    ).select_related("scheme").order_by("next_installment_date")
    serializer = SIPSerializer(sips, many=True)
    return Response({"count": sips.count(), "results": serializer.data})


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def sip_due(request):
    family_id = _active_family(request)
    if family_id is None:
        return Response({"error": "A valid active family is required."}, status=400)
    installments = SIPInstallment.objects.filter(
        sip__family_group_id=family_id,
        sip__is_active=True,
        status=SIPInstallmentStatus.DUE,
    ).select_related("sip", "sip__scheme").order_by(
        "scheduled_date", "sip__scheme__scheme_name"
    )
    results = []
    for installment in installments:
        results.append({
            "id": installment.id,
            "sip_id": installment.sip.id,
            "scheme": installment.sip.scheme.scheme_name,
            "amount": installment.amount,
            "frequency": installment.sip.frequency,
            "scheduled_date": installment.scheduled_date,
            "next_installment_date": installment.sip.next_installment_date,
            "status": installment.status,
        })
    return Response({"count": len(results), "results": results})


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def sip_summary(request):
    family_id = _active_family(request)
    if family_id is None:
        return Response({"error": "A valid active family is required."}, status=400)
    return Response(SIPSummaryService.get_summary_for_family(family_id))


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def sip_execute(request, sip_id):
    return Response({
        "error": "Direct SIP execution is deprecated. Execute a specific SIP installment instead.",
        "use_endpoint": "/api/mutual-funds/sip-installments/<installment_id>/execute/",
    }, status=410)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def sip_installment_execute(request, installment_id):
    family_id = _active_family(request)
    if family_id is None:
        return Response({"error": "A valid active family is required."}, status=400)
    try:
        installment = SIPInstallment.objects.select_related(
            "sip", "sip__scheme"
        ).get(
            id=installment_id,
            sip__family_group_id=family_id,
        )
    except SIPInstallment.DoesNotExist:
        return Response({"error": "SIP installment not found."}, status=404)

    try:
        transaction_record, updated_installment, holding = (
            SIPInstallmentExecutionService.execute_installment(installment)
        )
    except ValueError as exc:
        return Response({"error": str(exc)}, status=400)

    return Response({
        "message": "SIP installment executed successfully.",
        "installment": {
            "id": updated_installment.id,
            "scheduled_date": updated_installment.scheduled_date,
            "amount": updated_installment.amount,
            "status": updated_installment.status,
            "transaction_id": updated_installment.transaction_id,
        },
        "transaction": {
            "id": transaction_record.id,
            "transaction_date": transaction_record.transaction_date,
            "units": transaction_record.units,
            "nav": transaction_record.nav,
            "amount": transaction_record.amount,
        },
        "holding": {
            "units": holding.units,
            "invested_value": holding.invested_value,
            "current_value": holding.current_value,
            "unrealized_pnl": holding.unrealized_pnl,
        },
    }, status=200)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
@ensure_csrf_cookie
def csrf_token(request):
    return Response({"detail": "CSRF cookie set."})
