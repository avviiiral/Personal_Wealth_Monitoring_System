import traceback
from decimal import Decimal
from typing import cast

from django.db import transaction
from django.db.models import Sum

from investments.models import (
    Asset,
    AssetCategory,
    Holding,
    Transaction,
    TransactionEditHistory,
)

from investments.services.portfolio_metrics import PortfolioMetricsService
from market_data.services.market_data_manager import MarketDataManager
from portfolio.services.holding_engine import HoldingCalculationEngine
from portfolio.services.portfolio_position_engine import PortfolioPositionEngine

from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .serializers import (
    AssetSerializer,
    HoldingSerializer,
    TransactionSerializer,
    TransactionEditHistorySerializer,
)
from users.permissions import family_scope, require_active_family


@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated])
def portfolio_assets(request):
    if request.method == "GET":
        assets = family_scope(Asset.objects, request.user).order_by("name")
        return Response({"count": assets.count(), "results": AssetSerializer(assets, many=True).data})

    serializer = AssetSerializer(data=request.data)
    if not serializer.is_valid():
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    family = require_active_family(request.user)
    asset = cast(Asset, serializer.save(owner=request.user, family=family))
    market_data = {"success": False, "skipped": True, "reason": "Market data not requested."}
    if asset.category in ["STOCK", "ETF"]:
        try:
            market_data = MarketDataManager.fetch_and_rebuild(asset, period="1y")
        except Exception as exc:
            market_data = {"success": False, "skipped": False, "error": str(exc)}
    asset_data = dict(AssetSerializer(asset).data)
    asset_data["market_data"] = market_data
    return Response(asset_data, status=status.HTTP_201_CREATED)


@api_view(["GET", "PUT", "PATCH", "DELETE"])
@permission_classes([IsAuthenticated])
def portfolio_asset_detail(request, asset_id):
    try:
        asset = family_scope(Asset.objects, request.user).filter(id=asset_id).first()
    if asset is None:
        return Response({"detail": "Asset not found."}, status=status.HTTP_404_NOT_FOUND)
    except Asset.DoesNotExist:
        return Response({"detail": "Asset not found."}, status=status.HTTP_404_NOT_FOUND)
    if request.method == "GET":
        return Response(AssetSerializer(asset).data, status=status.HTTP_200_OK)
    if request.method == "PUT":
        serializer = AssetSerializer(asset, data=request.data)
    elif request.method == "PATCH":
        serializer = AssetSerializer(asset, data=request.data, partial=True)
    else:
        asset.is_active = False
        asset.save(update_fields=["is_active", "updated_at"])
        return Response(status=status.HTTP_204_NO_CONTENT)
    if not serializer.is_valid():
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    asset = cast(Asset, serializer.save())
    return Response(AssetSerializer(asset).data, status=status.HTTP_200_OK)


@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated])
def portfolio_transactions(request):
    if request.method == "GET":
        transactions = (
            family_scope(Transaction.objects, request.user)
            .select_related("asset")
            .order_by("-transaction_date", "-created_at")
        )
        return Response({"count": transactions.count(), "results": TransactionSerializer(transactions, many=True).data})
    serializer = TransactionSerializer(data=request.data, context={"request": request})
    if not serializer.is_valid():
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    with transaction.atomic():
        family = require_active_family(request.user)
        transaction_obj = cast(Transaction, serializer.save(owner=request.user, family=family))
        HoldingCalculationEngine.rebuild_holding(transaction_obj.asset)
        PortfolioPositionEngine.rebuild_all_for_user(request.user)
    return Response(TransactionSerializer(transaction_obj).data, status=status.HTTP_201_CREATED)


def _transaction_history_snapshot(transaction_obj):
    return {
        "family_name": transaction_obj.family_name,
        "portfolio": transaction_obj.portfolio,
        "asset_class": transaction_obj.asset_class,
        "sub_class": transaction_obj.sub_class,
        "asset_name": transaction_obj.asset_name,
        "underlying": transaction_obj.underlying,
        "advisors": transaction_obj.advisors,
        "transaction_date": str(transaction_obj.transaction_date),
        "transaction_type": transaction_obj.transaction_type,
        "quantity": str(transaction_obj.quantity),
        "price_per_unit": str(transaction_obj.price_per_unit),
        "amount": str(transaction_obj.amount),
        "fees": str(transaction_obj.fees),
        "notes": transaction_obj.notes,
        "source": transaction_obj.source,
        "source_key": transaction_obj.source_key,
        "asset_id": transaction_obj.asset_id,
        "isin": transaction_obj.asset.isin,
    }


@api_view(["GET", "PUT", "PATCH", "DELETE"])
@permission_classes([IsAuthenticated])
def portfolio_transaction_detail(request, transaction_id):
    try:
        transaction_obj = family_scope(Transaction.objects.select_related("asset"), request.user).filter(id=transaction_id).first()
    if transaction_obj is None:
        return Response({"detail": "Transaction not found."}, status=status.HTTP_404_NOT_FOUND)
    except Transaction.DoesNotExist:
        return Response({"detail": "Transaction not found."}, status=status.HTTP_404_NOT_FOUND)
    if request.method == "GET":
        return Response(TransactionSerializer(transaction_obj).data, status=status.HTTP_200_OK)
    if request.method == "DELETE":
        old_asset = transaction_obj.asset
        with transaction.atomic():
            transaction_obj.delete()
            HoldingCalculationEngine.rebuild_holding(old_asset)
            PortfolioPositionEngine.rebuild_all_for_user(request.user)
        return Response(status=status.HTTP_204_NO_CONTENT)
    serializer = TransactionSerializer(
        transaction_obj,
        data=request.data,
        partial=request.method == "PATCH",
        context={"request": request},
    )
    if not serializer.is_valid():
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    old_asset = transaction_obj.asset
    old_values = _transaction_history_snapshot(transaction_obj)
    with transaction.atomic():
        transaction_obj = cast(Transaction, serializer.save())
        new_asset = transaction_obj.asset
        new_values = _transaction_history_snapshot(transaction_obj)
        changed_fields = [field for field in new_values if old_values.get(field) != new_values.get(field)]
        if changed_fields:
            TransactionEditHistory.objects.create(
                transaction=transaction_obj,
                owner=transaction_obj.owner,
                edited_by=request.user,
                old_values={field: old_values[field] for field in changed_fields},
                new_values={field: new_values[field] for field in changed_fields},
                changed_fields=changed_fields,
            )
        HoldingCalculationEngine.rebuild_holding(old_asset)
        if new_asset.id != old_asset.id:
            HoldingCalculationEngine.rebuild_holding(new_asset)
        PortfolioPositionEngine.rebuild_all_for_user(request.user)
    return Response(TransactionSerializer(transaction_obj).data, status=status.HTTP_200_OK)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def portfolio_transaction_edit_history(request):
    history = (
        family_scope(TransactionEditHistory.objects, request.user)
        .select_related("transaction", "transaction__asset", "edited_by")
        .order_by("-edited_at")
    )
    return Response({"count": history.count(), "results": TransactionEditHistorySerializer(history, many=True).data})


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def portfolio_summary(request):
    holdings = family_scope(Holding.objects, request.user).filter(asset__is_active=True)
    total_invested = holdings.aggregate(total=Sum("invested_value"))["total"] or Decimal("0")
    total_current_value = holdings.aggregate(total=Sum("current_value"))["total"] or Decimal("0")
    total_unrealized_pnl = total_current_value - total_invested
    pnl_percentage = (total_unrealized_pnl / total_invested) * Decimal("100") if total_invested else Decimal("0")
    return Response({
        "total_invested": total_invested,
        "total_current_value": total_current_value,
        "total_unrealized_pnl": total_unrealized_pnl,
        "pnl_percentage": round(float(pnl_percentage), 2),
        "number_of_holdings": holdings.count(),
    })


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def portfolio_holdings(request):
    holdings = (
        family_scope(Holding.objects, request.user).filter(asset__is_active=True)
        .exclude(asset__category=AssetCategory.MUTUAL_FUND)
        .select_related("asset")
        .order_by("-current_value")
    )
    return Response({"count": holdings.count(), "results": HoldingSerializer(holdings, many=True).data})


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def portfolio_tree(request):
    from portfolio.services.portfolio_tree_service import PortfolioTreeService
    try:
        xirr_filters = {
            "family": request.query_params.get("family", "").strip(),
            "asset_class": request.query_params.get("asset_class", "").strip(),
            "advisor": request.query_params.get("advisor", "").strip(),
        }
        tree = PortfolioTreeService.build(
            owner=family_scope(Transaction.objects, request.user).values_list("owner_id", flat=True),
            xirr_filters=xirr_filters,
        )
    except Exception as exc:
        traceback.print_exc()
        return Response(
            {"success": False, "message": "Unable to build the portfolio tree.", "error": str(exc)},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
    return Response({"success": True, **tree}, status=status.HTTP_200_OK)
